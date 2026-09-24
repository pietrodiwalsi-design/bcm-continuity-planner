"""FastAPI + Jinja2 web workshop tool — the public-facing deliverable that
sits alongside (never replacing) the FastMCP server used via Claude
Desktop (`bcm_planner.mcp_server`). Both share the exact same underlying
business logic in `bia_engine.py` / `bcp_generator.py` /
`crisis_management.py` — this module never re-implements a calculation or
generation rule, it only wires HTTP routes -> those functions -> Jinja2
HTML / WeasyPrint PDF rendering.

Run locally:
    uvicorn bcm_planner.web.app:app --reload --port 8000
or:
    python -m bcm_planner.web.app

See docs/web_workshop_tool.md for the full design (tenant isolation
model, session/access-code flow, Render deployment).

Tenant isolation (the core requirement — see docs/web_workshop_tool.md):
every route under `/session/{session_token}/...` first resolves
`session_token` -> `organization_id` via `tenancy.resolve_session`, then
threads that `organization_id` through every subsequent
`tenancy.get_*_scoped` call before reading/writing any entity. A
`TenantMismatchError` or `SessionNotFoundError` is always mapped to a
plain HTTP 404 (never 403) so a mismatched/guessed ID or token cannot
even be distinguished from "does not exist" by response code.
"""

from __future__ import annotations

import datetime
import os
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.exceptions import HTTPException as StarletteHTTPException

from bcm_planner import bcp_generator, bia_engine, crisis_management, db
from bcm_planner.web import pdf_export, queries, tenancy, workshop


def _safe_filename(name: str, extension: str = "pdf") -> str:
    """Sanitizes a display name (which may contain non-Latin-1 characters
    such as the em-dash used in bcp_generator's auto-generated plan_title,
    e.g. 'Payment Processing \u2014 Operational Business Continuity Plan')
    into a Content-Disposition-safe ASCII filename. Content-Disposition
    header values must be latin-1-encodable; a raw f-string with an
    em-dash raises UnicodeEncodeError inside Starlette's header encoder
    (found during local smoke-testing of the BCP export route).
    """
    ascii_name = name.encode("ascii", errors="ignore").decode("ascii")
    ascii_name = "".join(c if c.isalnum() or c in (" ", "-", "_") else "_" for c in ascii_name)
    ascii_name = "_".join(ascii_name.split()) or "export"
    return f"{ascii_name}.{extension}"

_TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"

app = FastAPI(title="BCM Continuity Planner — Workshop Tool")
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

_STATIC_DIR = Path(__file__).resolve().parent / "static"
if _STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")


# ---------------------------------------------------------------------------
# Error handling — TenantMismatchError / SessionNotFoundError / NotFoundError
# always surface as a plain 404, never leaking which case it was.
# ---------------------------------------------------------------------------


@app.exception_handler(tenancy.TenantMismatchError)
@app.exception_handler(tenancy.SessionNotFoundError)
@app.exception_handler(bia_engine.NotFoundError)
async def _not_found_handler(request: Request, exc: Exception) -> Response:
    return templates.TemplateResponse(
        request, "error_404.html", {"detail": "Not found."}, status_code=404
    )


@app.exception_handler(bia_engine.BCMPlannerError)
async def _bcm_error_handler(request: Request, exc: Exception) -> Response:
    return templates.TemplateResponse(
        request, "error_400.html", {"detail": str(exc)}, status_code=400
    )


def _base_url(request: Request) -> str:
    return str(request.base_url).rstrip("/")


# ---------------------------------------------------------------------------
# Home / session creation
# ---------------------------------------------------------------------------


@app.get("/", response_class=HTMLResponse)
async def home(request: Request) -> Response:
    return templates.TemplateResponse(request, "home.html", {"new_session": None})


@app.post("/session/create", response_class=HTMLResponse)
async def create_session(
    request: Request,
    organization_name: str = Form(...),
    bcms_scope_description: str = Form(...),
    industry: Optional[str] = Form(None),
    workshop_label: Optional[str] = Form(None),
) -> Response:
    with db.get_connection() as conn:
        result = workshop.create_workshop_session(
            conn,
            organization_name=organization_name,
            bcms_scope_description=bcms_scope_description,
            industry=industry or None,
            workshop_label=workshop_label or None,
        )
    return templates.TemplateResponse(
        request,
        "home.html",
        {"new_session": result, "base_url": _base_url(request)},
    )


@app.get("/session/goto")
async def session_goto(token: str) -> Response:
    with db.get_connection() as conn:
        tenancy.resolve_session(conn, token)  # raises SessionNotFoundError -> 404 if invalid
    return RedirectResponse(url=f"/session/{token}/", status_code=303)


# ---------------------------------------------------------------------------
# Session-scoped dashboard
# ---------------------------------------------------------------------------


def _resolve(conn, session_token: str) -> dict[str, Any]:
    """Every session-scoped route calls this first. Returns the resolved
    web_workshop_sessions row (organization_id, admin_user_id, ...).
    """
    return tenancy.resolve_session(conn, session_token)


@app.get("/session/{session_token}/", response_class=HTMLResponse)
async def dashboard(request: Request, session_token: str) -> Response:
    with db.get_connection() as conn:
        session = _resolve(conn, session_token)
        org_id = session["organization_id"]
        organization = bia_engine.get_organization(conn, org_id)
        activities = queries.list_activities_by_organization(conn, org_id)
        bc_plans = bcp_generator.list_bc_plans_by_organization(conn, org_id)
        cmt_roles = crisis_management.list_cmt_roles_by_organization(conn, org_id)
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "session_token": session_token,
            "organization": organization,
            "activities": activities,
            "bc_plans": bc_plans,
            "cmt_roles": cmt_roles,
        },
    )


# ---------------------------------------------------------------------------
# BIA — scope hierarchy + assessments + gap analysis + SPOF
# ---------------------------------------------------------------------------


def _build_activity_details(conn, activities: list[dict[str, Any]]) -> list[dict[str, Any]]:
    activity_details = []
    for activity in activities:
        bia_list = bia_engine.list_bia_assessments_by_activity(conn, activity["activity_id"])
        strategies = []
        gaps = []
        for bia in bia_list:
            strategies.extend(bia_engine.list_recovery_strategies_by_bia(conn, bia["bia_id"]))
            gaps.extend(bia_engine.list_gap_analyses_by_bia(conn, bia["bia_id"]))
        spofs = bia_engine.detect_single_points_of_failure(conn, activity["activity_id"])
        impact_matrix = bia_engine.get_impact_matrix_grid(conn, "activity", activity["activity_id"])
        activity_details.append(
            {
                "activity": activity,
                "bia_assessments": bia_list,
                "recovery_strategies": strategies,
                "gap_analyses": gaps,
                "spofs": spofs,
                "impact_matrix": impact_matrix,
            }
        )
    return activity_details


@app.get("/session/{session_token}/bia", response_class=HTMLResponse)
async def bia_page(request: Request, session_token: str) -> Response:
    with db.get_connection() as conn:
        session = _resolve(conn, session_token)
        org_id = session["organization_id"]
        organization = bia_engine.get_organization(conn, org_id)
        products = queries.list_products_services_by_organization(conn, org_id)
        units = queries.list_business_units_by_organization(conn, org_id)
        processes = queries.list_business_processes_by_organization(conn, org_id)
        activities = queries.list_activities_by_organization(conn, org_id)
        resources = queries.list_resources_by_organization(conn, org_id)

        product_details = [
            {"product": p, "impact_matrix": bia_engine.get_impact_matrix_grid(conn, "product_service", p["product_service_id"])}
            for p in products
        ]
        process_details = [
            {"process": p, "impact_matrix": bia_engine.get_impact_matrix_grid(conn, "business_process", p["process_id"])}
            for p in processes
        ]
        activity_details = _build_activity_details(conn, activities)
        resource_details = [
            {"resource": r, "measures": bia_engine.list_resource_recovery_measures(conn, r["resource_id"])}
            for r in resources
        ]
    return templates.TemplateResponse(
        request,
        "bia.html",
        {
            "session_token": session_token,
            "organization": organization,
            "products": products,
            "product_details": product_details,
            "units": units,
            "processes": processes,
            "process_details": process_details,
            "activities": activities,
            "activity_details": activity_details,
            "resource_details": resource_details,
            "impact_categories": bia_engine.IMPACT_MATRIX_CATEGORIES,
            "impact_category_labels": bia_engine.IMPACT_MATRIX_CATEGORY_LABELS,
            "impact_timeframes": bia_engine.IMPACT_MATRIX_TIMEFRAMES,
            "impact_timeframe_labels": bia_engine.IMPACT_MATRIX_TIMEFRAME_LABELS,
            "impact_severities": bia_engine.IMPACT_MATRIX_SEVERITIES,
            "recovery_measure_statuses": bia_engine.RESOURCE_RECOVERY_MEASURE_STATUSES,
        },
    )


@app.post("/session/{session_token}/bia/unit")
async def create_unit(session_token: str, name: str = Form(...)) -> Response:
    with db.get_connection() as conn:
        session = _resolve(conn, session_token)
        bia_engine.create_business_unit(conn, session["admin_user_id"], session["organization_id"], name)
    return RedirectResponse(url=f"/session/{session_token}/bia?flash=Business+unit+created", status_code=303)


@app.post("/session/{session_token}/bia/unit/{unit_id}/edit")
async def edit_unit(session_token: str, unit_id: str, name: str = Form(...), code: Optional[str] = Form(None)) -> Response:
    with db.get_connection() as conn:
        session = _resolve(conn, session_token)
        tenancy.get_business_unit_scoped(conn, session["organization_id"], unit_id)
        bia_engine.update_business_unit(conn, session["admin_user_id"], unit_id, name=name, code=code or None)
    return RedirectResponse(url=f"/session/{session_token}/bia?flash=Business+unit+updated", status_code=303)


@app.post("/session/{session_token}/bia/product")
async def create_product(
    session_token: str, name: str = Form(...), description: Optional[str] = Form(None),
    worst_case_scenario: Optional[str] = Form(None),
) -> Response:
    with db.get_connection() as conn:
        session = _resolve(conn, session_token)
        product = bia_engine.create_product_service(
            conn, session["admin_user_id"], session["organization_id"], name, description=description or None,
        )
        if worst_case_scenario:
            bia_engine.update_product_service(
                conn, session["admin_user_id"], product["product_service_id"], worst_case_scenario=worst_case_scenario,
            )
    return RedirectResponse(url=f"/session/{session_token}/bia?flash=Product%2Fservice+created", status_code=303)


@app.post("/session/{session_token}/bia/product/{product_service_id}/edit")
async def edit_product(
    session_token: str, product_service_id: str, name: str = Form(...),
    description: Optional[str] = Form(None), worst_case_scenario: Optional[str] = Form(None),
) -> Response:
    with db.get_connection() as conn:
        session = _resolve(conn, session_token)
        tenancy.get_product_service_scoped(conn, session["organization_id"], product_service_id)
        bia_engine.update_product_service(
            conn, session["admin_user_id"], product_service_id, name=name,
            description=description or None, worst_case_scenario=worst_case_scenario or None,
        )
    return RedirectResponse(url=f"/session/{session_token}/bia?flash=Product%2Fservice+updated", status_code=303)


@app.post("/session/{session_token}/bia/process")
async def create_process(
    session_token: str, unit_id: str = Form(...), name: str = Form(...), process_owner: str = Form(...),
    product_service_id: Optional[str] = Form(None), worst_case_scenario: Optional[str] = Form(None),
) -> Response:
    with db.get_connection() as conn:
        session = _resolve(conn, session_token)
        # Verify the unit belongs to this org before using it (tenant isolation).
        unit = bia_engine.get_business_unit(conn, unit_id)
        if str(unit["organization_id"]) != str(session["organization_id"]):
            raise tenancy.TenantMismatchError("Business unit does not belong to this workshop's organization.")
        if product_service_id:
            tenancy.get_product_service_scoped(conn, session["organization_id"], product_service_id)
        process = bia_engine.create_business_process(
            conn, session["admin_user_id"], unit_id, name, process_owner,
            product_service_id=product_service_id or None,
        )
        if worst_case_scenario:
            bia_engine.update_business_process(
                conn, session["admin_user_id"], process["process_id"], worst_case_scenario=worst_case_scenario,
            )
    return RedirectResponse(url=f"/session/{session_token}/bia?flash=Business+process+created", status_code=303)


@app.post("/session/{session_token}/bia/process/{process_id}/edit")
async def edit_process(
    session_token: str, process_id: str, name: str = Form(...), process_owner: str = Form(...),
    worst_case_scenario: Optional[str] = Form(None),
) -> Response:
    with db.get_connection() as conn:
        session = _resolve(conn, session_token)
        tenancy.get_business_process_scoped(conn, session["organization_id"], process_id)
        bia_engine.update_business_process(
            conn, session["admin_user_id"], process_id, name=name, process_owner=process_owner,
            worst_case_scenario=worst_case_scenario or None,
        )
    return RedirectResponse(url=f"/session/{session_token}/bia?flash=Business+process+updated", status_code=303)


@app.post("/session/{session_token}/bia/activity")
async def create_activity_route(
    session_token: str, process_id: str = Form(...), name: str = Form(...), activity_owner: str = Form(...),
    worst_case_scenario: Optional[str] = Form(None),
) -> Response:
    with db.get_connection() as conn:
        session = _resolve(conn, session_token)
        # Tenant isolation: verify the process belongs to this org first.
        tenancy.get_business_process_scoped(conn, session["organization_id"], process_id)
        activity = bia_engine.create_activity(conn, session["admin_user_id"], process_id, name, activity_owner)
        if worst_case_scenario:
            bia_engine.update_activity(
                conn, session["admin_user_id"], activity["activity_id"], worst_case_scenario=worst_case_scenario,
            )
    return RedirectResponse(url=f"/session/{session_token}/bia?flash=Activity+created", status_code=303)


@app.post("/session/{session_token}/bia/activity/{activity_id}/edit")
async def edit_activity_route(
    session_token: str, activity_id: str, name: str = Form(...), activity_owner: str = Form(...),
    worst_case_scenario: Optional[str] = Form(None),
) -> Response:
    with db.get_connection() as conn:
        session = _resolve(conn, session_token)
        tenancy.get_activity_scoped(conn, session["organization_id"], activity_id)
        bia_engine.update_activity(
            conn, session["admin_user_id"], activity_id, name=name, activity_owner=activity_owner,
            worst_case_scenario=worst_case_scenario or None,
        )
    return RedirectResponse(url=f"/session/{session_token}/bia?flash=Activity+updated", status_code=303)


@app.post("/session/{session_token}/bia/assessment")
async def create_assessment(
    session_token: str,
    activity_id: str = Form(...),
    assessor_name: str = Form(...),
    mtpd_hours: int = Form(...),
    rto_hours: int = Form(...),
    rpo_hours: Optional[int] = Form(None),
    mbco_percentage: float = Form(100.0),
) -> Response:
    with db.get_connection() as conn:
        session = _resolve(conn, session_token)
        tenancy.get_activity_scoped(conn, session["organization_id"], activity_id)
        bia_engine.create_bia_assessment(
            conn, session["admin_user_id"], activity_id, assessor_name, datetime.date.today(),
            mtpd_hours, rto_hours, rpo_hours=rpo_hours, mbco_percentage=mbco_percentage,
        )
    return RedirectResponse(url=f"/session/{session_token}/bia?flash=BIA+assessment+recorded", status_code=303)


@app.post("/session/{session_token}/bia/assessment/{bia_id}/edit")
async def edit_assessment(
    session_token: str,
    bia_id: str,
    assessor_name: str = Form(...),
    mtpd_hours: int = Form(...),
    rto_hours: int = Form(...),
    rpo_hours: Optional[int] = Form(None),
    mbco_percentage: float = Form(100.0),
) -> Response:
    with db.get_connection() as conn:
        session = _resolve(conn, session_token)
        tenancy.get_bia_assessment_scoped(conn, session["organization_id"], bia_id)
        bia_engine.update_bia_assessment(
            conn, session["admin_user_id"], bia_id, assessor_name=assessor_name, mtpd_hours=mtpd_hours,
            rto_hours=rto_hours, rpo_hours=rpo_hours, mbco_percentage=mbco_percentage,
        )
    return RedirectResponse(url=f"/session/{session_token}/bia?flash=BIA+assessment+updated", status_code=303)


@app.post("/session/{session_token}/bia/gap")
async def create_gap(
    session_token: str,
    bia_id: str = Form(...),
    current_recovery_capability_hours: int = Form(...),
    target_rto_hours: int = Form(...),
    risk_summary: str = Form(...),
    identified_spof: Optional[str] = Form(None),
) -> Response:
    with db.get_connection() as conn:
        session = _resolve(conn, session_token)
        tenancy.get_bia_assessment_scoped(conn, session["organization_id"], bia_id)
        bia_engine.create_gap_analysis(
            conn, session["admin_user_id"], bia_id, current_recovery_capability_hours,
            target_rto_hours, risk_summary, identified_spof=identified_spof or None,
        )
    return RedirectResponse(url=f"/session/{session_token}/bia?flash=Gap+analysis+recorded", status_code=303)


@app.post("/session/{session_token}/bia/gap/{gap_id}/edit")
async def edit_gap(
    session_token: str,
    gap_id: str,
    current_recovery_capability_hours: int = Form(...),
    target_rto_hours: int = Form(...),
    risk_summary: str = Form(...),
    identified_spof: Optional[str] = Form(None),
) -> Response:
    with db.get_connection() as conn:
        session = _resolve(conn, session_token)
        tenancy.get_gap_analysis_scoped(conn, session["organization_id"], gap_id)
        bia_engine.update_gap_analysis(
            conn, session["admin_user_id"], gap_id,
            current_recovery_capability_hours=current_recovery_capability_hours,
            target_rto_hours=target_rto_hours, risk_summary=risk_summary,
            identified_spof=identified_spof or None,
        )
    return RedirectResponse(url=f"/session/{session_token}/bia?flash=Gap+analysis+updated", status_code=303)


@app.post("/session/{session_token}/bia/strategy")
async def create_strategy(
    session_token: str,
    bia_id: str = Form(...),
    category: str = Form(...),
    strategy_name: str = Form(...),
    description: str = Form(...),
    is_selected_option: Optional[str] = Form(None),
) -> Response:
    with db.get_connection() as conn:
        session = _resolve(conn, session_token)
        tenancy.get_bia_assessment_scoped(conn, session["organization_id"], bia_id)
        bia_engine.create_recovery_strategy(
            conn, session["admin_user_id"], bia_id, category, strategy_name, description,
            is_selected_option=bool(is_selected_option),
        )
    return RedirectResponse(url=f"/session/{session_token}/bia?flash=Recovery+strategy+recorded", status_code=303)


@app.post("/session/{session_token}/bia/strategy/{strategy_id}/edit")
async def edit_strategy(
    session_token: str,
    strategy_id: str,
    category: str = Form(...),
    strategy_name: str = Form(...),
    description: str = Form(...),
    is_selected_option: Optional[str] = Form(None),
) -> Response:
    with db.get_connection() as conn:
        session = _resolve(conn, session_token)
        strategy = tenancy.get_recovery_strategy_scoped(conn, session["organization_id"], strategy_id)
        bia_engine.update_recovery_strategy(
            conn, session["admin_user_id"], strategy_id, category=category,
            strategy_name=strategy_name, description=description,
        )
        if bool(is_selected_option):
            bia_engine.select_recovery_strategy(conn, session["admin_user_id"], strategy["bia_id"], strategy_id)
    return RedirectResponse(url=f"/session/{session_token}/bia?flash=Recovery+strategy+updated", status_code=303)


@app.post("/session/{session_token}/bia/resource")
async def create_resource_dependency(
    session_token: str,
    activity_id: str = Form(...),
    resource_name: str = Form(...),
    resource_type: str = Form(...),
    is_single_point_of_failure: Optional[str] = Form(None),
    minimum_quantity_required: int = Form(1),
) -> Response:
    with db.get_connection() as conn:
        session = _resolve(conn, session_token)
        tenancy.get_activity_scoped(conn, session["organization_id"], activity_id)
        resource = bia_engine.create_resource(
            conn, session["admin_user_id"], session["organization_id"], resource_type, resource_name,
            is_single_point_of_failure=bool(is_single_point_of_failure),
        )
        bia_engine.link_activity_resource_dependency(
            conn, session["admin_user_id"], activity_id, resource["resource_id"],
            minimum_quantity_required=minimum_quantity_required,
        )
    return RedirectResponse(url=f"/session/{session_token}/bia?flash=Resource+dependency+recorded", status_code=303)


@app.post("/session/{session_token}/bia/resource/{resource_id}/edit")
async def edit_resource(
    session_token: str,
    resource_id: str,
    name: str = Form(...),
    resource_type: str = Form(...),
    is_single_point_of_failure: Optional[str] = Form(None),
) -> Response:
    with db.get_connection() as conn:
        session = _resolve(conn, session_token)
        tenancy.get_resource_scoped(conn, session["organization_id"], resource_id)
        bia_engine.update_resource(
            conn, session["admin_user_id"], resource_id, name=name, resource_type=resource_type,
            is_single_point_of_failure=bool(is_single_point_of_failure),
        )
    return RedirectResponse(url=f"/session/{session_token}/bia?flash=Resource+updated", status_code=303)


@app.post("/session/{session_token}/bia/resource/{resource_id}/measure")
async def create_recovery_measure(
    session_token: str,
    resource_id: str,
    measure_type: str = Form(...),
    description: str = Form(...),
    recovery_time_hours: Optional[int] = Form(None),
    status: str = Form("not_started"),
    owner: Optional[str] = Form(None),
    estimated_cost: Optional[float] = Form(None),
) -> Response:
    with db.get_connection() as conn:
        session = _resolve(conn, session_token)
        tenancy.get_resource_scoped(conn, session["organization_id"], resource_id)
        bia_engine.create_resource_recovery_measure(
            conn, session["admin_user_id"], resource_id, measure_type, description,
            recovery_time_hours=recovery_time_hours, status=status, owner=owner or None,
            estimated_cost=estimated_cost,
        )
    return RedirectResponse(url=f"/session/{session_token}/bia?flash=Recovery+measure+recorded", status_code=303)


@app.post("/session/{session_token}/bia/measure/{measure_id}/edit")
async def edit_recovery_measure(
    session_token: str,
    measure_id: str,
    measure_type: str = Form(...),
    description: str = Form(...),
    recovery_time_hours: Optional[int] = Form(None),
    status: str = Form("not_started"),
    owner: Optional[str] = Form(None),
    estimated_cost: Optional[float] = Form(None),
) -> Response:
    with db.get_connection() as conn:
        session = _resolve(conn, session_token)
        tenancy.get_resource_recovery_measure_scoped(conn, session["organization_id"], measure_id)
        bia_engine.update_resource_recovery_measure(
            conn, session["admin_user_id"], measure_id, measure_type=measure_type, description=description,
            recovery_time_hours=recovery_time_hours, status=status, owner=owner or None,
            estimated_cost=estimated_cost,
        )
    return RedirectResponse(url=f"/session/{session_token}/bia?flash=Recovery+measure+updated", status_code=303)


@app.post("/session/{session_token}/bia/impact-matrix")
async def upsert_impact_matrix(
    session_token: str,
    scope_type: str = Form(...),
    scope_id: str = Form(...),
    category: str = Form(...),
    timeframe_hours: int = Form(...),
    severity: str = Form(...),
    notes: Optional[str] = Form(None),
) -> Response:
    with db.get_connection() as conn:
        session = _resolve(conn, session_token)
        # Tenant isolation: verify scope_id belongs to this org before writing.
        if scope_type == "product_service":
            tenancy.get_product_service_scoped(conn, session["organization_id"], scope_id)
        elif scope_type == "business_process":
            tenancy.get_business_process_scoped(conn, session["organization_id"], scope_id)
        elif scope_type == "activity":
            tenancy.get_activity_scoped(conn, session["organization_id"], scope_id)
        else:
            raise bia_engine.BCMPlannerError(f"Invalid scope_type {scope_type!r}.")
        bia_engine.upsert_impact_matrix_entry(
            conn, session["admin_user_id"], scope_type, scope_id, category, timeframe_hours, severity,
            notes=notes or None,
        )
    return RedirectResponse(url=f"/session/{session_token}/bia?flash=Impact+matrix+updated", status_code=303)


@app.get("/session/{session_token}/bia/export.pdf")
async def export_bia_pdf(session_token: str) -> Response:
    with db.get_connection() as conn:
        session = _resolve(conn, session_token)
        org_id = session["organization_id"]
        organization = bia_engine.get_organization(conn, org_id)
        activities = queries.list_activities_by_organization(conn, org_id)
        activity_details = []
        for activity in activities:
            bia_list = bia_engine.list_bia_assessments_by_activity(conn, activity["activity_id"])
            strategies = []
            gaps = []
            for bia in bia_list:
                strategies.extend(bia_engine.list_recovery_strategies_by_bia(conn, bia["bia_id"]))
                gaps.extend(bia_engine.list_gap_analyses_by_bia(conn, bia["bia_id"]))
            spofs = bia_engine.detect_single_points_of_failure(conn, activity["activity_id"])
            activity_details.append(
                {
                    "activity": activity,
                    "bia_assessments": bia_list,
                    "recovery_strategies": strategies,
                    "gap_analyses": gaps,
                    "spofs": spofs,
                }
            )
        pdf_bytes = pdf_export.render_bia_pdf(organization, activity_details)
    filename = _safe_filename(f"BIA_{organization['name']}")
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ---------------------------------------------------------------------------
# BCP — generate + view + export
# ---------------------------------------------------------------------------


@app.get("/session/{session_token}/bcp", response_class=HTMLResponse)
async def bcp_page(request: Request, session_token: str) -> Response:
    with db.get_connection() as conn:
        session = _resolve(conn, session_token)
        org_id = session["organization_id"]
        organization = bia_engine.get_organization(conn, org_id)
        bc_plans = bcp_generator.list_bc_plans_by_organization(conn, org_id)

        # BIAs with a selected recovery strategy, available for BCP generation.
        activities = queries.list_activities_by_organization(conn, org_id)
        generatable = []
        for activity in activities:
            for bia in bia_engine.list_bia_assessments_by_activity(conn, activity["activity_id"]):
                strategies = bia_engine.list_recovery_strategies_by_bia(conn, bia["bia_id"])
                selected = [s for s in strategies if s["is_selected_option"]]
                if selected:
                    generatable.append({"activity": activity, "bia": bia, "strategy": selected[0]})
    return templates.TemplateResponse(
        request,
        "bcp.html",
        {
            "session_token": session_token,
            "organization": organization,
            "bc_plans": bc_plans,
            "generatable": generatable,
        },
    )


@app.post("/session/{session_token}/bcp/generate")
async def generate_bcp(session_token: str, bia_id: str = Form(...)) -> Response:
    with db.get_connection() as conn:
        session = _resolve(conn, session_token)
        tenancy.get_bia_assessment_scoped(conn, session["organization_id"], bia_id)
        result = bcp_generator.generate_bcp_draft_from_bia(conn, session["admin_user_id"], bia_id)
        bcp_generator.generate_bau_return_phases(conn, session["admin_user_id"], result["plan"]["plan_id"])
    return RedirectResponse(
        url=f"/session/{session_token}/bcp/{result['plan']['plan_id']}?flash=BCP+draft+generated",
        status_code=303,
    )


@app.get("/session/{session_token}/bcp/{plan_id}", response_class=HTMLResponse)
async def bcp_detail(request: Request, session_token: str, plan_id: str) -> Response:
    with db.get_connection() as conn:
        session = _resolve(conn, session_token)
        organization = bia_engine.get_organization(conn, session["organization_id"])
        plan = tenancy.get_bc_plan_scoped(conn, session["organization_id"], plan_id)
        combined = queries.list_bcp_action_steps_and_bau_for_org(conn, plan_id)
    return templates.TemplateResponse(
        request,
        "bcp_detail.html",
        {
            "session_token": session_token,
            "organization": organization,
            "plan": plan,
            "action_steps": combined["action_steps"],
            "bau_phases": combined["bau_phases"],
        },
    )


@app.get("/session/{session_token}/bcp/{plan_id}/export.pdf")
async def export_bcp_pdf(session_token: str, plan_id: str) -> Response:
    with db.get_connection() as conn:
        session = _resolve(conn, session_token)
        organization = bia_engine.get_organization(conn, session["organization_id"])
        plan = tenancy.get_bc_plan_scoped(conn, session["organization_id"], plan_id)
        combined = queries.list_bcp_action_steps_and_bau_for_org(conn, plan_id)
        pdf_bytes = pdf_export.render_bcp_pdf(
            organization, plan, combined["action_steps"], combined["bau_phases"]
        )
    filename = _safe_filename(f"BCP_{plan['plan_title']}")
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ---------------------------------------------------------------------------
# CMT — roles, escalation triggers, export
# ---------------------------------------------------------------------------


@app.get("/session/{session_token}/cmt", response_class=HTMLResponse)
async def cmt_page(request: Request, session_token: str) -> Response:
    with db.get_connection() as conn:
        session = _resolve(conn, session_token)
        org_id = session["organization_id"]
        organization = bia_engine.get_organization(conn, org_id)
        cmt_roles = crisis_management.list_cmt_roles_by_organization(conn, org_id)
        escalation_triggers = crisis_management.list_escalation_triggers_by_organization(conn, org_id)
    return templates.TemplateResponse(
        request,
        "cmt.html",
        {
            "session_token": session_token,
            "organization": organization,
            "cmt_roles": cmt_roles,
            "escalation_triggers": escalation_triggers,
        },
    )


@app.post("/session/{session_token}/cmt/role")
async def create_cmt_role_route(
    session_token: str,
    role_name: str = Form(...),
    primary_assignee_name: str = Form(...),
    primary_assignee_phone: str = Form(...),
    primary_assignee_email: str = Form(...),
    key_responsibilities: str = Form(...),
    alternate_assignee_name: Optional[str] = Form(None),
    alternate_assignee_phone: Optional[str] = Form(None),
) -> Response:
    with db.get_connection() as conn:
        session = _resolve(conn, session_token)
        crisis_management.create_cmt_role(
            conn, session["admin_user_id"], session["organization_id"], role_name,
            primary_assignee_name, primary_assignee_phone, primary_assignee_email,
            key_responsibilities, alternate_assignee_name=alternate_assignee_name or None,
            alternate_assignee_phone=alternate_assignee_phone or None,
        )
    return RedirectResponse(url=f"/session/{session_token}/cmt?flash=CMT+role+added", status_code=303)


@app.post("/session/{session_token}/cmt/trigger")
async def create_escalation_trigger_route(
    session_token: str,
    severity_level: str = Form(...),
    incident_condition: str = Form(...),
    notification_timeframe_minutes: int = Form(...),
    required_action: str = Form(...),
) -> Response:
    with db.get_connection() as conn:
        session = _resolve(conn, session_token)
        crisis_management.create_escalation_trigger(
            conn, session["admin_user_id"], session["organization_id"], severity_level,
            incident_condition, notification_timeframe_minutes, required_action,
        )
    return RedirectResponse(url=f"/session/{session_token}/cmt?flash=Escalation+trigger+added", status_code=303)


@app.get("/session/{session_token}/cmt/export.pdf")
async def export_cmt_pdf(session_token: str) -> Response:
    with db.get_connection() as conn:
        session = _resolve(conn, session_token)
        org_id = session["organization_id"]
        organization = bia_engine.get_organization(conn, org_id)
        cmt_roles = crisis_management.list_cmt_roles_by_organization(conn, org_id)
        escalation_triggers = crisis_management.list_escalation_triggers_by_organization(conn, org_id)
        pdf_bytes = pdf_export.render_cmt_pdf(organization, cmt_roles, escalation_triggers)
    filename = _safe_filename(f"CMT_{organization['name']}")
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ---------------------------------------------------------------------------
# Health check (used by Dockerfile HEALTHCHECK / Render healthCheckPath)
# ---------------------------------------------------------------------------


@app.get("/healthz")
async def healthz() -> dict[str, Any]:
    return {"status": "ok", "db": db.healthcheck()}


def main() -> None:  # pragma: no cover - thin entrypoint, exercised via uvicorn in Docker/Render
    import uvicorn

    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run("bcm_planner.web.app:app", host="0.0.0.0", port=port)


if __name__ == "__main__":  # pragma: no cover
    main()
