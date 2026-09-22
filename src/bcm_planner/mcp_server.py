#!/usr/bin/env python3
"""BCM Continuity Planner — Model Context Protocol (MCP) Server.

FastMCP server exposing the Phase 1 BIA Engine as MCP tools over stdio,
consistent with the pattern used across Peter van Walsem's other IT risk
tooling repos (pqc-cbom-risk-auditor, ai-risk-auditor, vendor-soc-isae-auditor,
stride-threat-modeler).

Every tool opens a short-lived Postgres connection via bcm_planner.db,
delegates to bcm_planner.bia_engine, and returns the resulting row(s) or a
clear error. RBAC and audit logging are enforced inside bia_engine, not here.
"""

from __future__ import annotations

import datetime
from typing import Any, Optional

from fastmcp import FastMCP

from bcm_planner import bia_engine, db

mcp = FastMCP(
    name="bcm-continuity-planner-mcp",
    version="0.1.0",
    instructions=(
        "BIA Engine (Phase 1) for the BCM Continuity Planner: scope/hierarchy "
        "CRUD, impact matrix configuration, MTPD/RTO/RPO/MBCO capture with "
        "enforced RTO<MTPD, gap analysis with single-point-of-failure "
        "detection, and recovery strategy selection. All write tools require "
        "a valid application_users.user_id with a permitted role."
    ),
)


def _err(exc: Exception) -> dict[str, Any]:
    return {"error": type(exc).__name__, "message": str(exc)}


# ---------------------------------------------------------------------------
# Scope / hierarchy
# ---------------------------------------------------------------------------


@mcp.tool()
def create_organization(
    user_id: str, name: str, bcms_scope_description: str, industry: Optional[str] = None
) -> dict[str, Any]:
    """Creates a new organization (top of the BIA scope hierarchy)."""
    try:
        with db.get_connection() as conn:
            return bia_engine.create_organization(conn, user_id, name, bcms_scope_description, industry)
    except bia_engine.BCMPlannerError as exc:
        return _err(exc)


@mcp.tool()
def get_organization(organization_id: str) -> dict[str, Any]:
    """Reads a single organization by ID."""
    try:
        with db.get_connection() as conn:
            return bia_engine.get_organization(conn, organization_id)
    except bia_engine.BCMPlannerError as exc:
        return _err(exc)


@mcp.tool()
def create_business_unit(
    user_id: str, organization_id: str, name: str,
    code: Optional[str] = None, head_of_unit: Optional[str] = None,
) -> dict[str, Any]:
    """Creates a business unit under an organization."""
    try:
        with db.get_connection() as conn:
            return bia_engine.create_business_unit(conn, user_id, organization_id, name, code, head_of_unit)
    except bia_engine.BCMPlannerError as exc:
        return _err(exc)


@mcp.tool()
def create_product_service(
    user_id: str, organization_id: str, name: str,
    description: Optional[str] = None, priority_ranking: Optional[int] = None,
) -> dict[str, Any]:
    """Creates a product/service entry under an organization."""
    try:
        with db.get_connection() as conn:
            return bia_engine.create_product_service(conn, user_id, organization_id, name, description, priority_ranking)
    except bia_engine.BCMPlannerError as exc:
        return _err(exc)


@mcp.tool()
def create_business_process(
    user_id: str, unit_id: str, name: str, process_owner: str,
    product_service_id: Optional[str] = None, is_outsourced: bool = False,
) -> dict[str, Any]:
    """Creates a business process under a business unit, optionally linked to a product/service."""
    try:
        with db.get_connection() as conn:
            return bia_engine.create_business_process(conn, user_id, unit_id, name, process_owner, product_service_id, is_outsourced)
    except bia_engine.BCMPlannerError as exc:
        return _err(exc)


@mcp.tool()
def create_activity(
    user_id: str, process_id: str, name: str, activity_owner: str,
    parent_activity_id: Optional[str] = None, description: Optional[str] = None,
    is_prioritised: bool = False,
) -> dict[str, Any]:
    """Creates an activity under a process, optionally as a child of another activity."""
    try:
        with db.get_connection() as conn:
            return bia_engine.create_activity(conn, user_id, process_id, name, activity_owner, parent_activity_id, description, is_prioritised)
    except bia_engine.BCMPlannerError as exc:
        return _err(exc)


@mcp.tool()
def get_activity_tree(process_id: str) -> list[dict[str, Any]]:
    """Returns the nested parent/child activity tree for a process."""
    with db.get_connection() as conn:
        return bia_engine.get_activity_tree(conn, process_id)


@mcp.tool()
def create_resource(
    user_id: str, organization_id: str, resource_type: str, name: str,
    description: Optional[str] = None, location: Optional[str] = None,
    is_single_point_of_failure: bool = False, contact_details: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Creates a resource (personnel/technology/facility/equipment/supplier)."""
    try:
        with db.get_connection() as conn:
            return bia_engine.create_resource(
                conn, user_id, organization_id, resource_type, name, description,
                location, is_single_point_of_failure, contact_details,
            )
    except bia_engine.BCMPlannerError as exc:
        return _err(exc)


@mcp.tool()
def link_activity_resource_dependency(
    user_id: str, activity_id: str, resource_id: str,
    dependency_type: Optional[str] = None, minimum_quantity_required: int = 1,
    notes: Optional[str] = None,
) -> dict[str, Any]:
    """Links a resource as a dependency of an activity."""
    try:
        with db.get_connection() as conn:
            return bia_engine.link_activity_resource_dependency(
                conn, user_id, activity_id, resource_id, dependency_type, minimum_quantity_required, notes
            )
    except bia_engine.BCMPlannerError as exc:
        return _err(exc)


@mcp.tool()
def get_activity_resource_dependencies(activity_id: str) -> list[dict[str, Any]]:
    """Lists all resource dependencies for an activity, joined with resource details."""
    with db.get_connection() as conn:
        return bia_engine.get_activity_resource_dependencies(conn, activity_id)


# ---------------------------------------------------------------------------
# Impact matrix configuration
# ---------------------------------------------------------------------------


@mcp.tool()
def create_impact_category(
    user_id: str, organization_id: str, category_type: str, name: str, description: Optional[str] = None
) -> dict[str, Any]:
    """Creates an impact category (financial/operational/reputational/legal_regulatory/health_safety)."""
    try:
        with db.get_connection() as conn:
            return bia_engine.create_impact_category(conn, user_id, organization_id, category_type, name, description)
    except bia_engine.BCMPlannerError as exc:
        return _err(exc)


@mcp.tool()
def create_impact_threshold(
    user_id: str, category_id: str, severity: str, time_timeframe_hours: int,
    qualitative_criteria: str, financial_cost_min: Optional[float] = None,
    financial_cost_max: Optional[float] = None,
) -> dict[str, Any]:
    """Creates a severity threshold row for an impact category."""
    try:
        with db.get_connection() as conn:
            return bia_engine.create_impact_threshold(
                conn, user_id, category_id, severity, time_timeframe_hours,
                qualitative_criteria, financial_cost_min, financial_cost_max,
            )
    except bia_engine.BCMPlannerError as exc:
        return _err(exc)


# ---------------------------------------------------------------------------
# MTPD / RTO / RPO / MBCO
# ---------------------------------------------------------------------------


@mcp.tool()
def create_bia_assessment(
    user_id: str, activity_id: str, assessor_name: str, assessment_date: str,
    mtpd_hours: int, rto_hours: int, rpo_hours: Optional[int] = None,
    mbco_percentage: float = 100.00,
) -> dict[str, Any]:
    """Creates a BIA assessment (MTPD/RTO/RPO/MBCO) for an activity.

    RTO must be strictly less than MTPD — violations return a clear error
    (RTOConstraintViolation) instead of a raw database traceback.
    `assessment_date` is an ISO date string (YYYY-MM-DD).
    """
    try:
        parsed_date = datetime.date.fromisoformat(assessment_date)
        with db.get_connection() as conn:
            return bia_engine.create_bia_assessment(
                conn, user_id, activity_id, assessor_name, parsed_date,
                mtpd_hours, rto_hours, rpo_hours, mbco_percentage,
            )
    except bia_engine.BCMPlannerError as exc:
        return _err(exc)


@mcp.tool()
def update_bia_assessment(user_id: str, bia_id: str, fields: dict[str, Any]) -> dict[str, Any]:
    """Updates mutable fields on an existing BIA assessment.

    `fields` may include: assessor_name, assessment_date (ISO string),
    mtpd_hours, rto_hours, rpo_hours, mbco_percentage, status, approved_by,
    approval_date (ISO string). RTO<MTPD is re-validated if either changes.
    """
    try:
        parsed = dict(fields)
        if "assessment_date" in parsed and isinstance(parsed["assessment_date"], str):
            parsed["assessment_date"] = datetime.date.fromisoformat(parsed["assessment_date"])
        if "approval_date" in parsed and isinstance(parsed["approval_date"], str):
            parsed["approval_date"] = datetime.date.fromisoformat(parsed["approval_date"])
        with db.get_connection() as conn:
            return bia_engine.update_bia_assessment(conn, user_id, bia_id, **parsed)
    except bia_engine.BCMPlannerError as exc:
        return _err(exc)


@mcp.tool()
def get_bia_assessment(bia_id: str) -> dict[str, Any]:
    """Reads a single BIA assessment by ID."""
    try:
        with db.get_connection() as conn:
            return bia_engine.get_bia_assessment(conn, bia_id)
    except bia_engine.BCMPlannerError as exc:
        return _err(exc)


@mcp.tool()
def list_bia_assessments_by_activity(activity_id: str) -> list[dict[str, Any]]:
    """Lists all BIA assessments for a given activity, most recent first."""
    with db.get_connection() as conn:
        return bia_engine.list_bia_assessments_by_activity(conn, activity_id)


# ---------------------------------------------------------------------------
# Gap analysis
# ---------------------------------------------------------------------------


@mcp.tool()
def create_gap_analysis(
    user_id: str, bia_id: str, current_recovery_capability_hours: int,
    target_rto_hours: int, risk_summary: str, identified_spof: Optional[str] = None,
) -> dict[str, Any]:
    """Creates a gap analysis for a BIA assessment. gap_hours is computed by
    the database (GENERATED ALWAYS AS STORED) and returned, never written.
    """
    try:
        with db.get_connection() as conn:
            return bia_engine.create_gap_analysis(
                conn, user_id, bia_id, current_recovery_capability_hours,
                target_rto_hours, risk_summary, identified_spof,
            )
    except bia_engine.BCMPlannerError as exc:
        return _err(exc)


@mcp.tool()
def list_gap_analyses_by_bia(bia_id: str) -> list[dict[str, Any]]:
    """Lists all gap analyses for a BIA assessment."""
    with db.get_connection() as conn:
        return bia_engine.list_gap_analyses_by_bia(conn, bia_id)


@mcp.tool()
def detect_single_points_of_failure(activity_id: str) -> list[dict[str, Any]]:
    """Flags resource dependencies of an activity where the resource is
    marked as a single point of failure (resources.is_single_point_of_failure).
    """
    with db.get_connection() as conn:
        return bia_engine.detect_single_points_of_failure(conn, activity_id)


# ---------------------------------------------------------------------------
# Recovery strategy selection
# ---------------------------------------------------------------------------


@mcp.tool()
def create_recovery_strategy(
    user_id: str, bia_id: str, category: str, strategy_name: str, description: str,
    estimated_implementation_cost: Optional[float] = None, prerequisites: Optional[str] = None,
    is_selected_option: bool = False,
) -> dict[str, Any]:
    """Creates a recovery strategy option for a BIA assessment.

    If is_selected_option=True, any previously-selected strategy for the
    same bia_id is un-marked in the same transaction (only one selected
    strategy per bia_id, enforced in application logic).
    """
    try:
        with db.get_connection() as conn:
            return bia_engine.create_recovery_strategy(
                conn, user_id, bia_id, category, strategy_name, description,
                estimated_implementation_cost, prerequisites, is_selected_option,
            )
    except bia_engine.BCMPlannerError as exc:
        return _err(exc)


@mcp.tool()
def select_recovery_strategy(user_id: str, bia_id: str, strategy_id: str) -> dict[str, Any]:
    """Marks a recovery strategy as selected for a BIA assessment,
    un-marking any previously-selected strategy for that same bia_id.
    """
    try:
        with db.get_connection() as conn:
            return bia_engine.select_recovery_strategy(conn, user_id, bia_id, strategy_id)
    except bia_engine.BCMPlannerError as exc:
        return _err(exc)


@mcp.tool()
def list_recovery_strategies_by_bia(bia_id: str) -> list[dict[str, Any]]:
    """Lists all recovery strategy options for a BIA assessment."""
    with db.get_connection() as conn:
        return bia_engine.list_recovery_strategies_by_bia(conn, bia_id)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
