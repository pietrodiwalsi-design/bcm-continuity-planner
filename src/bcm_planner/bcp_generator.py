"""BCP Generator & Return-to-BAU module — Phase 2 of the BCM Continuity Planner.

Implements, against the PostgreSQL schema in schema/001_core_schema.sql and
schema/002_rbac_approvals_review_additions.sql, covering FR7-FR8:

- CRUD for bc_plans (strategic/tactical/operational Business Continuity
  Plans) and bcp_action_steps.
- CRUD for bau_return_procedures (Return to BAU phases).
- Rule-based auto-generation of a draft bc_plan + starter bcp_action_steps
  from an existing Phase 1 bia_assessments row + its activity's resource
  dependencies + a selected recovery_strategies row
  (`generate_bcp_draft_from_bia`).
- Rule-based auto-generation of the standard 4-phase Return-to-BAU set for
  an existing bc_plan (`generate_bau_return_phases`).

The exact mapping rules (recovery-strategy-category -> action step
templates, resource-type -> responsible role, standard BAU phases) are
documented in detail in docs/bcp_generation_rules.md — this module is the
single source of truth for the actual template content; that file is a
human-readable summary of it.

Conventions reused, unchanged, from bia_engine.py (Phase 1) per the Phase 2
brief — this module does not reinvent RBAC, audit logging, error handling,
or connection style:

- psycopg (v3), dict-row cursors via bcm_planner.db.get_connection().
- `bia_engine.require_role` for RBAC (same WRITE_ROLES allow-list).
- `bia_engine.log_audit` for append-only audit logging.
- `bia_engine.BCMPlannerError` / `NotFoundError` / `InsufficientRoleError`
  as the shared exception hierarchy (this module adds one new leaf
  exception, `InvalidStepNumberError`).
- The same SAVEPOINT + DB-CHECK-constraint-translation pattern used for
  `chk_rto_less_than_mtpd` in bia_engine.py, applied here to
  bcp_action_steps' `CHECK (step_number > 0)` constraint.

Provenance (traceability from an auto-generated bc_plan back to its source
BIA/activity/recovery strategy) is recorded via a `document_versions` row
(schema/002, added for FR15 version history) rather than a new bc_plans
column — see docs/bcp_generation_rules.md section 3 for the rationale.
"""

from __future__ import annotations

from typing import Any, Optional
from uuid import UUID

import psycopg
from psycopg.types.json import Jsonb

from bcm_planner.bia_engine import (
    BCMPlannerError,
    InsufficientRoleError,
    NotFoundError,
    WRITE_ROLES,
    require_role,
    log_audit,
    get_bia_assessment,
    get_activity,
    get_activity_resource_dependencies,
    list_recovery_strategies_by_bia,
)

__all__ = [
    "BCMPlannerError",
    "InsufficientRoleError",
    "NotFoundError",
    "InvalidStepNumberError",
    "create_bc_plan",
    "get_bc_plan",
    "update_bc_plan",
    "list_bc_plans_by_organization",
    "create_bcp_action_step",
    "get_bcp_action_step",
    "update_bcp_action_step",
    "list_bcp_action_steps_by_plan",
    "generate_bcp_draft_from_bia",
    "get_bc_plan_provenance",
    "create_bau_return_procedure",
    "get_bau_return_procedure",
    "update_bau_return_procedure",
    "list_bau_return_procedures_by_plan",
    "generate_bau_return_phases",
    "CATEGORY_ACTION_STEP_TEMPLATES",
    "RESOURCE_TYPE_RESPONSIBLE_ROLE",
    "STANDARD_BAU_RETURN_PHASES",
]


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class InvalidStepNumberError(BCMPlannerError):
    """Raised when step_number <= 0 (bcp_action_steps.step_number > 0
    CHECK constraint), whether caught by the Python pre-check or translated
    from the database constraint — same pattern as bia_engine's
    RTOConstraintViolation.
    """


# ---------------------------------------------------------------------------
# bc_plans CRUD
# ---------------------------------------------------------------------------


def create_bc_plan(
    conn: psycopg.Connection,
    user_id: UUID | str,
    organization_id: UUID | str,
    plan_tier: str,
    plan_title: str,
    plan_owner: str,
    invocation_criteria: str,
    version: str = "1.0",
    alternate_facility_details: Optional[str] = None,
    status: str = "Draft",
) -> dict[str, Any]:
    """Creates a bc_plans row. plan_tier must be one of the
    plan_tier_enum values ('strategic', 'tactical', 'operational').
    """
    require_role(conn, user_id, WRITE_ROLES)
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO bc_plans
                (organization_id, plan_tier, plan_title, version, plan_owner, invocation_criteria, alternate_facility_details, status)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING *
            """,
            (
                str(organization_id), plan_tier, plan_title, version, plan_owner,
                invocation_criteria, alternate_facility_details, status,
            ),
        )
        row = cur.fetchone()
    log_audit(
        conn, user_id, "CREATE", "bc_plans", row["plan_id"],
        {"plan_tier": plan_tier, "plan_title": plan_title, "status": status},
        organization_id=organization_id,
    )
    return row


def get_bc_plan(conn: psycopg.Connection, plan_id: UUID | str) -> dict[str, Any]:
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM bc_plans WHERE plan_id = %s", (str(plan_id),))
        row = cur.fetchone()
    if row is None:
        raise NotFoundError(f"BC plan {plan_id} not found.")
    return row


def list_bc_plans_by_organization(conn: psycopg.Connection, organization_id: UUID | str) -> list[dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM bc_plans WHERE organization_id = %s ORDER BY created_at DESC",
            (str(organization_id),),
        )
        return cur.fetchall()


def update_bc_plan(
    conn: psycopg.Connection,
    user_id: UUID | str,
    plan_id: UUID | str,
    **fields: Any,
) -> dict[str, Any]:
    """Updates a subset of mutable bc_plans fields.

    Allowed fields: plan_tier, plan_title, version, plan_owner,
    invocation_criteria, alternate_facility_details, status.
    """
    require_role(conn, user_id, WRITE_ROLES)
    allowed_fields = {
        "plan_tier", "plan_title", "version", "plan_owner",
        "invocation_criteria", "alternate_facility_details", "status",
    }
    unknown = set(fields) - allowed_fields
    if unknown:
        raise BCMPlannerError(f"Unknown/immutable bc_plans field(s): {', '.join(sorted(unknown))}")
    if not fields:
        raise BCMPlannerError("update_bc_plan called with no fields to update.")

    set_clause = ", ".join(f"{k} = %s" for k in fields)
    params = list(fields.values()) + [str(plan_id)]
    with conn.cursor() as cur:
        cur.execute(f"UPDATE bc_plans SET {set_clause} WHERE plan_id = %s RETURNING *", params)
        row = cur.fetchone()
    if row is None:
        raise NotFoundError(f"BC plan {plan_id} not found.")
    log_audit(conn, user_id, "UPDATE", "bc_plans", plan_id, fields)
    return row


# ---------------------------------------------------------------------------
# bcp_action_steps CRUD
# ---------------------------------------------------------------------------


def _check_step_number(step_number: int) -> None:
    """Fast, friendly pre-check mirroring bcp_action_steps' CHECK
    (step_number > 0) constraint — same pattern as bia_engine's
    _check_rto_less_than_mtpd.
    """
    if step_number <= 0:
        raise InvalidStepNumberError(
            f"Invalid action step: step_number must be > 0, got {step_number}."
        )


def _translate_step_check_violation(exc: Exception, step_number: Optional[int] = None) -> None:
    """Re-raises a Postgres CHECK violation on bcp_action_steps.step_number
    as a clear InvalidStepNumberError instead of a raw psycopg traceback —
    same pattern as bia_engine._translate_db_error.
    """
    if isinstance(exc, psycopg.errors.CheckViolation):
        diag = getattr(exc, "diag", None)
        constraint = getattr(diag, "constraint_name", "") if diag else ""
        if "step_number" in (constraint or ""):
            detail = f" (submitted step_number={step_number})" if step_number is not None else ""
            raise InvalidStepNumberError(
                f"Database rejected this action step: step_number must be > 0{detail}."
            ) from exc
        raise BCMPlannerError(f"Database rejected the operation due to a data constraint: {exc}") from exc
    raise


def create_bcp_action_step(
    conn: psycopg.Connection,
    user_id: UUID | str,
    plan_id: UUID | str,
    step_number: int,
    responsible_role: str,
    action_title: str,
    detailed_instructions: str,
    timeframe_offset_minutes: Optional[int] = None,
) -> dict[str, Any]:
    """Creates a bcp_action_steps row. Enforces step_number > 0 via a fast
    Python pre-check first, then relies on the DB CHECK constraint as the
    authoritative backstop (translated into a clear error if it fires).
    """
    require_role(conn, user_id, WRITE_ROLES)
    _check_step_number(step_number)

    savepoint = "sp_create_bcp_step"
    with conn.cursor() as cur:
        cur.execute(f"SAVEPOINT {savepoint}")
        try:
            cur.execute(
                """
                INSERT INTO bcp_action_steps
                    (plan_id, step_number, responsible_role, action_title, detailed_instructions, timeframe_offset_minutes)
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING *
                """,
                (str(plan_id), step_number, responsible_role, action_title, detailed_instructions, timeframe_offset_minutes),
            )
            row = cur.fetchone()
        except psycopg.errors.CheckViolation as exc:
            cur.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
            _translate_step_check_violation(exc, step_number=step_number)
        else:
            cur.execute(f"RELEASE SAVEPOINT {savepoint}")

    log_audit(
        conn, user_id, "CREATE", "bcp_action_steps", row["step_id"],
        {"plan_id": str(plan_id), "step_number": step_number, "action_title": action_title},
    )
    return row


def get_bcp_action_step(conn: psycopg.Connection, step_id: UUID | str) -> dict[str, Any]:
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM bcp_action_steps WHERE step_id = %s", (str(step_id),))
        row = cur.fetchone()
    if row is None:
        raise NotFoundError(f"BCP action step {step_id} not found.")
    return row


def list_bcp_action_steps_by_plan(conn: psycopg.Connection, plan_id: UUID | str) -> list[dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM bcp_action_steps WHERE plan_id = %s ORDER BY step_number",
            (str(plan_id),),
        )
        return cur.fetchall()


def update_bcp_action_step(
    conn: psycopg.Connection,
    user_id: UUID | str,
    step_id: UUID | str,
    **fields: Any,
) -> dict[str, Any]:
    """Updates a subset of mutable bcp_action_steps fields.

    Allowed fields: step_number, responsible_role, action_title,
    detailed_instructions, timeframe_offset_minutes. step_number is
    re-validated (> 0) if present.
    """
    require_role(conn, user_id, WRITE_ROLES)
    allowed_fields = {
        "step_number", "responsible_role", "action_title",
        "detailed_instructions", "timeframe_offset_minutes",
    }
    unknown = set(fields) - allowed_fields
    if unknown:
        raise BCMPlannerError(f"Unknown/immutable bcp_action_steps field(s): {', '.join(sorted(unknown))}")
    if not fields:
        raise BCMPlannerError("update_bcp_action_step called with no fields to update.")

    if "step_number" in fields:
        _check_step_number(fields["step_number"])

    set_clause = ", ".join(f"{k} = %s" for k in fields)
    params = list(fields.values()) + [str(step_id)]

    savepoint = "sp_update_bcp_step"
    with conn.cursor() as cur:
        cur.execute(f"SAVEPOINT {savepoint}")
        try:
            cur.execute(
                f"UPDATE bcp_action_steps SET {set_clause} WHERE step_id = %s RETURNING *", params
            )
            row = cur.fetchone()
        except psycopg.errors.CheckViolation as exc:
            cur.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
            _translate_step_check_violation(exc, step_number=fields.get("step_number"))
        else:
            cur.execute(f"RELEASE SAVEPOINT {savepoint}")

    if row is None:
        raise NotFoundError(f"BCP action step {step_id} not found.")
    log_audit(conn, user_id, "UPDATE", "bcp_action_steps", step_id, fields)
    return row


# ---------------------------------------------------------------------------
# Internal helpers: organization resolution, singular recovery strategy read
# ---------------------------------------------------------------------------


def _resolve_organization_id_for_activity(conn: psycopg.Connection, activity_id: UUID | str) -> Any:
    """Walks activities -> business_processes -> business_units to find the
    owning organization_id, since activities do not carry it directly.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT bu.organization_id
            FROM activities a
            JOIN business_processes bp ON bp.process_id = a.process_id
            JOIN business_units bu ON bu.unit_id = bp.unit_id
            WHERE a.activity_id = %s
            """,
            (str(activity_id),),
        )
        row = cur.fetchone()
    if row is None:
        raise NotFoundError(f"Could not resolve organization_id for activity {activity_id}.")
    return row["organization_id"]


def _get_recovery_strategy(conn: psycopg.Connection, strategy_id: UUID | str) -> dict[str, Any]:
    """Singular recovery_strategies read. bia_engine only exposes a
    list-by-bia helper; this module needs a single-row lookup too.
    """
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM recovery_strategies WHERE strategy_id = %s", (str(strategy_id),))
        row = cur.fetchone()
    if row is None:
        raise NotFoundError(f"Recovery strategy {strategy_id} not found.")
    return row


# ---------------------------------------------------------------------------
# Rule-based BCP auto-generation (FR7) — see docs/bcp_generation_rules.md
# ---------------------------------------------------------------------------

# Recovery-strategy category -> starter action step templates.
# Each tuple: (responsible_role, action_title, detailed_instructions, timeframe_offset_minutes)
# See docs/bcp_generation_rules.md section 1.2 for the rationale behind
# each category's step sequence and timing.
CATEGORY_ACTION_STEP_TEMPLATES: dict[str, list[tuple[str, str, str, int]]] = {
    "active_active": [
        (
            "IT Operations Lead",
            "Confirm active-active failover status",
            "Verify that the active-active secondary site/environment is already handling live "
            "traffic/operations; confirm health and remaining capacity.",
            0,
        ),
        (
            "IT Operations Lead",
            "Rebalance load across active sites",
            "Adjust load balancing/routing configuration so full operational load is distributed "
            "across all remaining active-active sites given the disruption.",
            15,
        ),
        (
            "IT Operations Lead",
            "Monitor active site performance",
            "Continuously monitor the surviving active site(s) for capacity and performance "
            "degradation under full load until the disruption is resolved.",
            30,
        ),
    ],
    "hot_standby": [
        (
            "Recovery Team Lead",
            "Activate hot standby site",
            "Formally activate the pre-provisioned hot standby site/environment per its "
            "activation runbook.",
            0,
        ),
        (
            "IT Operations Lead",
            "Redirect traffic/operations to standby resources",
            "Redirect users, traffic, or manual operations from the primary site to the hot "
            "standby site/resources.",
            15,
        ),
        (
            "Recovery Team Lead",
            "Validate standby site operational status",
            "Confirm the hot standby site is fully operational and meeting the activity's RTO.",
            30,
        ),
    ],
    "warm_standby": [
        (
            "Recovery Team Lead",
            "Activate warm standby site",
            "Initiate activation of the partially-provisioned warm standby site; complete any "
            "remaining provisioning/configuration steps.",
            0,
        ),
        (
            "IT Operations Lead",
            "Scale up warm standby resources",
            "Scale up compute/personnel/facility resources at the warm standby site to full "
            "operating capacity.",
            30,
        ),
        (
            "Recovery Team Lead",
            "Redirect operations to warm standby",
            "Redirect traffic/operations to the warm standby site once validated as ready.",
            60,
        ),
    ],
    "cold_site": [
        (
            "Facilities/Recovery Lead",
            "Mobilize to cold site",
            "Dispatch personnel and equipment to the cold site location per the alternate "
            "facility plan.",
            0,
        ),
        (
            "IT Operations Lead",
            "Provision cold site infrastructure",
            "Install, configure, and test required technology/equipment at the cold site from "
            "scratch.",
            60,
        ),
        (
            "IT Operations Lead",
            "Restore data/systems at cold site",
            "Restore the most recent backups/data to the newly provisioned cold site "
            "infrastructure.",
            120,
        ),
    ],
    "work_from_home": [
        (
            "HR/Remote-Work Coordinator",
            "Activate work-from-home protocol",
            "Notify affected personnel to activate remote-working arrangements per the WFH "
            "continuity protocol.",
            0,
        ),
        (
            "IT Operations Lead",
            "Verify remote access and tooling",
            "Confirm personnel have working VPN/remote access, required applications, and "
            "communication tools.",
            15,
        ),
        (
            "Process Owner",
            "Redirect customer/process interactions to remote channels",
            "Ensure inbound/outbound activity interactions are routed through remote-capable "
            "channels.",
            30,
        ),
    ],
    "manual_workaround": [
        (
            "Process Owner",
            "Initiate manual process procedures",
            "Switch the affected activity to its documented manual/paper-based workaround "
            "procedure.",
            0,
        ),
        (
            "Process Owner",
            "Assign personnel to manual process execution",
            "Assign and brief available personnel on manual process roles and responsibilities.",
            15,
        ),
        (
            "Process Owner",
            "Track manual process backlog for later reconciliation",
            "Log all manually processed transactions/items for reconciliation once systems are "
            "restored.",
            30,
        ),
    ],
}

# Resource type -> responsible role for resource-dependency-derived steps.
# See docs/bcp_generation_rules.md section 1.2(b).
RESOURCE_TYPE_RESPONSIBLE_ROLE: dict[str, str] = {
    "personnel": "HR / Staffing Lead",
    "technology": "IT Operations Lead",
    "facility": "Facilities Lead",
    "equipment": "Facilities/Equipment Lead",
    "supplier": "Vendor Management Lead",
}

# Categories for which the recovery strategy implies a physical/technical
# alternate facility worth copying into bc_plans.alternate_facility_details.
_ALTERNATE_FACILITY_CATEGORIES = frozenset({"hot_standby", "warm_standby", "cold_site"})


def _record_bc_plan_provenance(
    conn: psycopg.Connection,
    plan_id: UUID | str,
    source_bia_id: UUID | str,
    source_activity_id: UUID | str,
    source_recovery_strategy_id: UUID | str,
) -> dict[str, Any]:
    """Writes a document_versions row (schema/002_..., entity_type='bc_plan')
    capturing full provenance of an auto-generated plan: the source BIA
    assessment, activity, and recovery strategy it was derived from.

    Chosen instead of a new bc_plans column per the Phase 2 brief ("prefer
    solving it within existing columns/JSONB first") — document_versions
    already provides exactly this JSONB snapshot mechanism (added for FR15
    version history in schema/002_rbac_approvals_review_additions.sql).
    See docs/bcp_generation_rules.md section 3.
    """
    snapshot = {
        "generation_method": "rule_based_auto_populate",
        "source_bia_id": str(source_bia_id),
        "source_activity_id": str(source_activity_id),
        "source_recovery_strategy_id": str(source_recovery_strategy_id),
    }
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO document_versions (entity_type, entity_id, version_label, snapshot_json, change_summary)
            VALUES ('bc_plan', %s, %s, %s, %s)
            RETURNING *
            """,
            (
                str(plan_id),
                "0.1-draft",
                Jsonb(snapshot),
                "Auto-generated draft BCP from BIA + recovery strategy (Phase 2 rule-based generator).",
            ),
        )
        return cur.fetchone()


def get_bc_plan_provenance(conn: psycopg.Connection, plan_id: UUID | str) -> Optional[dict[str, Any]]:
    """Reads back the most recent document_versions snapshot for a bc_plan,
    if one exists (i.e. the plan was auto-generated via
    generate_bcp_draft_from_bia). Returns None for hand-authored plans that
    were never auto-generated.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT * FROM document_versions
            WHERE entity_type = 'bc_plan' AND entity_id = %s
            ORDER BY created_at DESC LIMIT 1
            """,
            (str(plan_id),),
        )
        return cur.fetchone()


def generate_bcp_draft_from_bia(
    conn: psycopg.Connection,
    user_id: UUID | str,
    bia_id: UUID | str,
    plan_tier: str = "operational",
    plan_owner: Optional[str] = None,
    recovery_strategy_id: Optional[UUID | str] = None,
) -> dict[str, Any]:
    """Auto-populates a draft bc_plans row + starter bcp_action_steps from
    an existing bia_assessments row + its activity's resource dependencies
    + a selected (or explicitly given) recovery_strategies row.

    Rule-based, not AI-generated — see docs/bcp_generation_rules.md for the
    full mapping this function implements. Returns
    {"plan": <bc_plans row>, "action_steps": [<bcp_action_steps row>, ...]}.

    Raises NotFoundError if bia_id/activity/recovery strategy cannot be
    resolved (e.g. no recovery strategy has been selected yet for this BIA
    and recovery_strategy_id was not given explicitly).
    """
    require_role(conn, user_id, WRITE_ROLES)

    bia = get_bia_assessment(conn, bia_id)
    activity = get_activity(conn, bia["activity_id"])

    if recovery_strategy_id is not None:
        strategy = _get_recovery_strategy(conn, recovery_strategy_id)
    else:
        strategies = list_recovery_strategies_by_bia(conn, bia_id)
        selected = [s for s in strategies if s["is_selected_option"]]
        if not selected:
            raise NotFoundError(
                f"No selected recovery strategy found for BIA {bia_id}; select one via "
                "bia_engine.select_recovery_strategy first, or pass recovery_strategy_id explicitly."
            )
        strategy = selected[0]

    organization_id = _resolve_organization_id_for_activity(conn, activity["activity_id"])

    plan_title = f"{activity['name']} \u2014 {plan_tier.title()} Business Continuity Plan"

    invocation_criteria = (
        f"Invoke this plan when disruption to '{activity['name']}' is expected to exceed the "
        f"Recovery Time Objective (RTO) of {bia['rto_hours']}h and risks breaching the Maximum "
        f"Tolerable Period of Disruption (MTPD) of {bia['mtpd_hours']}h"
    )
    if bia.get("rpo_hours") is not None:
        invocation_criteria += (
            f", or when data loss risk exceeds the Recovery Point Objective (RPO) of "
            f"{bia['rpo_hours']}h"
        )
    invocation_criteria += (
        f". Minimum Business Continuity Objective (MBCO): {bia['mbco_percentage']}% of normal "
        f"capacity. (Auto-generated draft \u2014 source BIA: {bia['bia_id']}, "
        f"Activity: {activity['activity_id']}, Recovery Strategy: {strategy['strategy_id']} "
        f"[{strategy['category']}].)"
    )

    alternate_facility_details = (
        strategy["description"] if strategy["category"] in _ALTERNATE_FACILITY_CATEGORIES else None
    )

    plan_owner_final = plan_owner or activity["activity_owner"]

    plan = create_bc_plan(
        conn, user_id, organization_id, plan_tier, plan_title, plan_owner_final,
        invocation_criteria, version="0.1-draft",
        alternate_facility_details=alternate_facility_details, status="Draft",
    )

    dependencies = get_activity_resource_dependencies(conn, activity["activity_id"])
    category_templates = CATEGORY_ACTION_STEP_TEMPLATES.get(strategy["category"], [])

    steps_created: list[dict[str, Any]] = []
    step_number = 1
    for role, title, instructions, offset in category_templates:
        step = create_bcp_action_step(
            conn, user_id, plan["plan_id"], step_number, role, title, instructions, offset
        )
        steps_created.append(step)
        step_number += 1

    for dep in dependencies:
        role = RESOURCE_TYPE_RESPONSIBLE_ROLE.get(dep["resource_type"], "Recovery Team Lead")
        title = f"Confirm availability of {dep['resource_type']} resource: {dep['resource_name']}"
        instructions = (
            f"Verify that '{dep['resource_name']}' ({dep['resource_type']}) required for this "
            f"activity is available/allocated at the recovery site or workaround location "
            f"(minimum quantity required: {dep['minimum_quantity_required']})."
        )
        if dep.get("notes"):
            instructions += f" Notes: {dep['notes']}"
        step = create_bcp_action_step(
            conn, user_id, plan["plan_id"], step_number, role, title, instructions, None
        )
        steps_created.append(step)
        step_number += 1

    _record_bc_plan_provenance(
        conn, plan["plan_id"],
        source_bia_id=bia["bia_id"],
        source_activity_id=activity["activity_id"],
        source_recovery_strategy_id=strategy["strategy_id"],
    )

    log_audit(
        conn, user_id, "CREATE", "bc_plans", plan["plan_id"],
        {
            "auto_generated": True,
            "source_bia_id": str(bia["bia_id"]),
            "source_activity_id": str(activity["activity_id"]),
            "source_recovery_strategy_id": str(strategy["strategy_id"]),
            "action_step_count": len(steps_created),
        },
        organization_id=organization_id,
    )

    return {"plan": plan, "action_steps": steps_created}


# ---------------------------------------------------------------------------
# bau_return_procedures CRUD (FR8)
# ---------------------------------------------------------------------------


def create_bau_return_procedure(
    conn: psycopg.Connection,
    user_id: UUID | str,
    plan_id: UUID | str,
    phase_number: int,
    title: str,
    restoration_type: str,
    validation_criteria: str,
    action_steps: str,
) -> dict[str, Any]:
    require_role(conn, user_id, WRITE_ROLES)
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO bau_return_procedures
                (plan_id, phase_number, title, restoration_type, validation_criteria, action_steps)
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING *
            """,
            (str(plan_id), phase_number, title, restoration_type, validation_criteria, action_steps),
        )
        row = cur.fetchone()
    log_audit(
        conn, user_id, "CREATE", "bau_return_procedures", row["bau_procedure_id"],
        {"plan_id": str(plan_id), "phase_number": phase_number, "title": title},
    )
    return row


def get_bau_return_procedure(conn: psycopg.Connection, bau_procedure_id: UUID | str) -> dict[str, Any]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM bau_return_procedures WHERE bau_procedure_id = %s", (str(bau_procedure_id),)
        )
        row = cur.fetchone()
    if row is None:
        raise NotFoundError(f"BAU return procedure {bau_procedure_id} not found.")
    return row


def list_bau_return_procedures_by_plan(conn: psycopg.Connection, plan_id: UUID | str) -> list[dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM bau_return_procedures WHERE plan_id = %s ORDER BY phase_number",
            (str(plan_id),),
        )
        return cur.fetchall()


def update_bau_return_procedure(
    conn: psycopg.Connection,
    user_id: UUID | str,
    bau_procedure_id: UUID | str,
    **fields: Any,
) -> dict[str, Any]:
    """Updates a subset of mutable bau_return_procedures fields.

    Allowed fields: phase_number, title, restoration_type,
    validation_criteria, action_steps.
    """
    require_role(conn, user_id, WRITE_ROLES)
    allowed_fields = {"phase_number", "title", "restoration_type", "validation_criteria", "action_steps"}
    unknown = set(fields) - allowed_fields
    if unknown:
        raise BCMPlannerError(f"Unknown/immutable bau_return_procedures field(s): {', '.join(sorted(unknown))}")
    if not fields:
        raise BCMPlannerError("update_bau_return_procedure called with no fields to update.")

    set_clause = ", ".join(f"{k} = %s" for k in fields)
    params = list(fields.values()) + [str(bau_procedure_id)]
    with conn.cursor() as cur:
        cur.execute(
            f"UPDATE bau_return_procedures SET {set_clause} WHERE bau_procedure_id = %s RETURNING *",
            params,
        )
        row = cur.fetchone()
    if row is None:
        raise NotFoundError(f"BAU return procedure {bau_procedure_id} not found.")
    log_audit(conn, user_id, "UPDATE", "bau_return_procedures", bau_procedure_id, fields)
    return row


# ---------------------------------------------------------------------------
# Rule-based Return-to-BAU auto-generation (FR8) — see docs/bcp_generation_rules.md
# ---------------------------------------------------------------------------

# Standard 4-phase Return-to-BAU set. Each tuple:
# (phase_number, title, restoration_type, validation_criteria, action_steps)
# See docs/bcp_generation_rules.md section 2.
STANDARD_BAU_RETURN_PHASES: list[tuple[int, str, str, str, str]] = [
    (
        1,
        "Verify primary resource restoration",
        "Primary Resource Restoration",
        "Primary resource(s) (facility, systems, personnel, suppliers) confirmed physically/"
        "technically restored and meeting original operating specifications.",
        "Inspect and test restored primary resource(s) against pre-disruption specifications; "
        "obtain sign-off from the relevant resource owner before proceeding to parallel run.",
    ),
    (
        2,
        "Parallel run / validation",
        "Parallel Run And Validation",
        "Restored primary environment produces results consistent with the current "
        "recovery-mode environment over an agreed validation window, with no material "
        "discrepancies.",
        "Run the primary and recovery-mode environments in parallel for an agreed validation "
        "period; compare outputs/transactions/results and log any discrepancies for resolution "
        "before cutover.",
    ),
    (
        3,
        "Cutover to primary",
        "Cutover To Primary",
        "Cutover from recovery-mode operations to primary resources completed with zero "
        "unplanned data loss and no unresolved critical incidents during the cutover window.",
        "Execute the documented cutover runbook at the agreed cutover time; monitor key "
        "operational metrics during and immediately after cutover; stand down recovery-mode "
        "resources once primary operation is confirmed stable.",
    ),
    (
        4,
        "Post-incident review handoff",
        "Post-Incident Review Handoff",
        "Lessons learned, plan gaps, and improvement actions from the disruption and recovery "
        "are documented and formally handed off to the BIA/BCP owner.",
        "Conduct a post-incident review meeting with the recovery team and activity owner; "
        "document findings, gaps, and improvement actions; hand off the resulting action items "
        "to the BIA/BCP owner for plan maintenance (see FR15 / Phase 5).",
    ),
]


def generate_bau_return_phases(
    conn: psycopg.Connection,
    user_id: UUID | str,
    plan_id: UUID | str,
) -> list[dict[str, Any]]:
    """Auto-generates the standard 4-phase Return-to-BAU set for an
    existing bc_plan: Verify primary resource restoration -> Parallel run /
    validation -> Cutover to primary -> Post-incident review handoff.

    Rule-based (not derived from BIA parameters — the BAU return sequence
    is generic across recovery strategies). Intended for use once a
    bc_plan's recovery mode has been invoked/tested and primary resources
    are being restored; the schema does not enforce a plan-status gate for
    this (no DB constraint exists for it) so it can be called against a
    Draft plan too — a deliberate scope choice per REQUIREMENTS.md (no
    workflow-engine state machine in this tool).
    """
    require_role(conn, user_id, WRITE_ROLES)
    plan = get_bc_plan(conn, plan_id)  # raises NotFoundError if missing

    created: list[dict[str, Any]] = []
    for phase_number, title, restoration_type, validation_criteria, action_steps in STANDARD_BAU_RETURN_PHASES:
        row = create_bau_return_procedure(
            conn, user_id, plan["plan_id"], phase_number, title, restoration_type,
            validation_criteria, action_steps,
        )
        created.append(row)

    log_audit(
        conn, user_id, "CREATE", "bau_return_procedures", plan["plan_id"],
        {"auto_generated": True, "plan_id": str(plan_id), "phase_count": len(created)},
    )
    return created
