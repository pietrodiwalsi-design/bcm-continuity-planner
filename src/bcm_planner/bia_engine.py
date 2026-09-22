"""BIA Engine — Phase 1 core logic for the BCM Continuity Planner.

Implements, against the PostgreSQL schema in schema/001_core_schema.sql and
schema/002_rbac_approvals_review_additions.sql:

- Scope/hierarchy CRUD (organizations -> business_units -> products_services
  -> business_processes -> activities, with parent_activity_id support) plus
  resources and activity_resource_dependencies.
- Impact matrix configuration (impact_categories, impact_thresholds).
- MTPD/RTO/RPO/MBCO capture (bia_assessments), with the RTO < MTPD business
  rule enforced both as a friendly Python pre-check and as a translation
  layer around the DB CHECK constraint (chk_rto_less_than_mtpd).
- Gap analysis (gap_analyses) including a single-point-of-failure helper.
- Recovery strategy selection (recovery_strategies) with "only one selected
  per bia_id" enforced in application logic.
- A minimal RBAC helper gating writes by application_users.role.
- Append-only audit logging (audit_logs) for every create/update.

Every public write function takes a `user_id` (UUID of an application_users
row) as its first data argument (after `conn`) and enforces RBAC + writes an
audit log entry as part of the same operation. Scope: personal/demo tool
(see REQUIREMENTS.md) — this is intentionally not a full auth system.
"""

from __future__ import annotations

import datetime
from typing import Any, Iterable, Optional
from uuid import UUID

import psycopg
from psycopg.types.json import Jsonb

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class BCMPlannerError(Exception):
    """Base exception for all BIA engine errors."""


class InsufficientRoleError(BCMPlannerError):
    """Raised when the acting user's role does not permit the requested operation."""


class NotFoundError(BCMPlannerError):
    """Raised when a referenced entity does not exist."""


class RTOConstraintViolation(BCMPlannerError):
    """Raised when RTO >= MTPD, whether caught by the Python pre-check or
    translated from the database's chk_rto_less_than_mtpd CHECK constraint.
    """


# ---------------------------------------------------------------------------
# RBAC
# ---------------------------------------------------------------------------

# Roles permitted to create/update BIA engine data. Matches app_role_enum
# from schema/002_rbac_approvals_review_additions.sql. Approval/sign-off
# roles are intentionally excluded from *write* access here — approving is
# a distinct Phase 5 concern, not a BIA data-entry permission.
WRITE_ROLES: frozenset[str] = frozenset({"admin", "bia_assessor"})

# Roles permitted to mark a recovery strategy as selected. Kept the same as
# WRITE_ROLES for Phase 1 (no separate approval gate yet — that lands with
# the Phase 5 sign-off workflow).
STRATEGY_SELECTION_ROLES: frozenset[str] = WRITE_ROLES


def require_role(
    conn: psycopg.Connection,
    user_id: UUID | str,
    allowed_roles: Iterable[str],
) -> str:
    """Verifies that `user_id` exists, is active, and holds one of `allowed_roles`.

    Returns the user's role on success. Raises InsufficientRoleError
    otherwise, with a clear, actionable message (never a raw DB error).
    This is a real check against application_users.role — not decorative.
    """
    allowed = set(allowed_roles)
    with conn.cursor() as cur:
        cur.execute(
            "SELECT role, is_active, full_name FROM application_users WHERE user_id = %s",
            (str(user_id),),
        )
        row = cur.fetchone()

    if row is None:
        raise InsufficientRoleError(
            f"User {user_id} was not found in application_users; write operations are denied."
        )
    if not row["is_active"]:
        raise InsufficientRoleError(
            f"User {row['full_name']} ({user_id}) is deactivated (is_active=false); "
            "write operations are denied."
        )
    role = row["role"]
    if role not in allowed:
        raise InsufficientRoleError(
            f"User {row['full_name']} ({user_id}) has role '{role}', which does not "
            f"permit this operation. Required role(s): {', '.join(sorted(allowed))}."
        )
    return role


# ---------------------------------------------------------------------------
# Audit logging
# ---------------------------------------------------------------------------


def log_audit(
    conn: psycopg.Connection,
    user_id: UUID | str,
    action: str,
    entity_affected: str,
    entity_id: UUID | str,
    changes_json: Optional[dict[str, Any]] = None,
    organization_id: Optional[UUID | str] = None,
) -> None:
    """Writes one append-only row to audit_logs. Never updates or deletes rows."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO audit_logs (organization_id, user_id, action, entity_affected, entity_id, changes_json)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (
                str(organization_id) if organization_id else None,
                str(user_id),
                action,
                entity_affected,
                str(entity_id),
                Jsonb(changes_json) if changes_json is not None else None,
            ),
        )


# ---------------------------------------------------------------------------
# Scope / hierarchy CRUD
# ---------------------------------------------------------------------------


def create_organization(
    conn: psycopg.Connection,
    user_id: UUID | str,
    name: str,
    bcms_scope_description: str,
    industry: Optional[str] = None,
) -> dict[str, Any]:
    require_role(conn, user_id, WRITE_ROLES)
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO organizations (name, industry, bcms_scope_description)
            VALUES (%s, %s, %s)
            RETURNING *
            """,
            (name, industry, bcms_scope_description),
        )
        row = cur.fetchone()
    log_audit(
        conn, user_id, "CREATE", "organizations", row["organization_id"],
        {"name": name, "industry": industry}, organization_id=row["organization_id"],
    )
    return row


def get_organization(conn: psycopg.Connection, organization_id: UUID | str) -> dict[str, Any]:
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM organizations WHERE organization_id = %s", (str(organization_id),))
        row = cur.fetchone()
    if row is None:
        raise NotFoundError(f"Organization {organization_id} not found.")
    return row


def create_business_unit(
    conn: psycopg.Connection,
    user_id: UUID | str,
    organization_id: UUID | str,
    name: str,
    code: Optional[str] = None,
    head_of_unit: Optional[str] = None,
) -> dict[str, Any]:
    require_role(conn, user_id, WRITE_ROLES)
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO business_units (organization_id, name, code, head_of_unit)
            VALUES (%s, %s, %s, %s)
            RETURNING *
            """,
            (str(organization_id), name, code, head_of_unit),
        )
        row = cur.fetchone()
    log_audit(
        conn, user_id, "CREATE", "business_units", row["unit_id"],
        {"name": name, "code": code}, organization_id=organization_id,
    )
    return row


def get_business_unit(conn: psycopg.Connection, unit_id: UUID | str) -> dict[str, Any]:
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM business_units WHERE unit_id = %s", (str(unit_id),))
        row = cur.fetchone()
    if row is None:
        raise NotFoundError(f"Business unit {unit_id} not found.")
    return row


def create_product_service(
    conn: psycopg.Connection,
    user_id: UUID | str,
    organization_id: UUID | str,
    name: str,
    description: Optional[str] = None,
    priority_ranking: Optional[int] = None,
) -> dict[str, Any]:
    require_role(conn, user_id, WRITE_ROLES)
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO products_services (organization_id, name, description, priority_ranking)
            VALUES (%s, %s, %s, %s)
            RETURNING *
            """,
            (str(organization_id), name, description, priority_ranking),
        )
        row = cur.fetchone()
    log_audit(
        conn, user_id, "CREATE", "products_services", row["product_service_id"],
        {"name": name}, organization_id=organization_id,
    )
    return row


def get_product_service(conn: psycopg.Connection, product_service_id: UUID | str) -> dict[str, Any]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM products_services WHERE product_service_id = %s", (str(product_service_id),)
        )
        row = cur.fetchone()
    if row is None:
        raise NotFoundError(f"Product/service {product_service_id} not found.")
    return row


def create_business_process(
    conn: psycopg.Connection,
    user_id: UUID | str,
    unit_id: UUID | str,
    name: str,
    process_owner: str,
    product_service_id: Optional[UUID | str] = None,
    is_outsourced: bool = False,
) -> dict[str, Any]:
    require_role(conn, user_id, WRITE_ROLES)
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO business_processes (unit_id, product_service_id, name, process_owner, is_outsourced)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING *
            """,
            (str(unit_id), str(product_service_id) if product_service_id else None, name, process_owner, is_outsourced),
        )
        row = cur.fetchone()
    log_audit(conn, user_id, "CREATE", "business_processes", row["process_id"], {"name": name})
    return row


def get_business_process(conn: psycopg.Connection, process_id: UUID | str) -> dict[str, Any]:
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM business_processes WHERE process_id = %s", (str(process_id),))
        row = cur.fetchone()
    if row is None:
        raise NotFoundError(f"Business process {process_id} not found.")
    return row


def create_activity(
    conn: psycopg.Connection,
    user_id: UUID | str,
    process_id: UUID | str,
    name: str,
    activity_owner: str,
    parent_activity_id: Optional[UUID | str] = None,
    description: Optional[str] = None,
    is_prioritised: bool = False,
) -> dict[str, Any]:
    require_role(conn, user_id, WRITE_ROLES)
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO activities (process_id, parent_activity_id, name, description, activity_owner, is_prioritised)
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING *
            """,
            (
                str(process_id),
                str(parent_activity_id) if parent_activity_id else None,
                name,
                description,
                activity_owner,
                is_prioritised,
            ),
        )
        row = cur.fetchone()
    log_audit(conn, user_id, "CREATE", "activities", row["activity_id"], {"name": name, "parent_activity_id": str(parent_activity_id) if parent_activity_id else None})
    return row


def get_activity(conn: psycopg.Connection, activity_id: UUID | str) -> dict[str, Any]:
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM activities WHERE activity_id = %s", (str(activity_id),))
        row = cur.fetchone()
    if row is None:
        raise NotFoundError(f"Activity {activity_id} not found.")
    return row


def list_child_activities(conn: psycopg.Connection, parent_activity_id: UUID | str) -> list[dict[str, Any]]:
    """Returns direct child activities of a given parent activity (one level down)."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM activities WHERE parent_activity_id = %s ORDER BY name",
            (str(parent_activity_id),),
        )
        return cur.fetchall()


def list_activities_by_process(conn: psycopg.Connection, process_id: UUID | str) -> list[dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM activities WHERE process_id = %s ORDER BY name", (str(process_id),)
        )
        return cur.fetchall()


def get_activity_tree(conn: psycopg.Connection, process_id: UUID | str) -> list[dict[str, Any]]:
    """Builds a nested parent/child activity tree for a process (root activities
    are those with parent_activity_id IS NULL), for use by the dashboard export.
    """
    all_activities = list_activities_by_process(conn, process_id)
    by_id = {str(a["activity_id"]): dict(a, children=[]) for a in all_activities}
    roots: list[dict[str, Any]] = []
    for a in all_activities:
        node = by_id[str(a["activity_id"])]
        parent_id = a.get("parent_activity_id")
        if parent_id and str(parent_id) in by_id:
            by_id[str(parent_id)]["children"].append(node)
        else:
            roots.append(node)
    return roots


# ---------------------------------------------------------------------------
# Resources & dependencies
# ---------------------------------------------------------------------------


def create_resource(
    conn: psycopg.Connection,
    user_id: UUID | str,
    organization_id: UUID | str,
    resource_type: str,
    name: str,
    description: Optional[str] = None,
    location: Optional[str] = None,
    is_single_point_of_failure: bool = False,
    contact_details: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    require_role(conn, user_id, WRITE_ROLES)
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO resources (organization_id, resource_type, name, description, location, is_single_point_of_failure, contact_details)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            RETURNING *
            """,
            (
                str(organization_id),
                resource_type,
                name,
                description,
                location,
                is_single_point_of_failure,
                Jsonb(contact_details) if contact_details is not None else None,
            ),
        )
        row = cur.fetchone()
    log_audit(
        conn, user_id, "CREATE", "resources", row["resource_id"],
        {"name": name, "resource_type": resource_type, "is_single_point_of_failure": is_single_point_of_failure},
        organization_id=organization_id,
    )
    return row


def get_resource(conn: psycopg.Connection, resource_id: UUID | str) -> dict[str, Any]:
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM resources WHERE resource_id = %s", (str(resource_id),))
        row = cur.fetchone()
    if row is None:
        raise NotFoundError(f"Resource {resource_id} not found.")
    return row


def link_activity_resource_dependency(
    conn: psycopg.Connection,
    user_id: UUID | str,
    activity_id: UUID | str,
    resource_id: UUID | str,
    dependency_type: Optional[str] = None,
    minimum_quantity_required: int = 1,
    notes: Optional[str] = None,
) -> dict[str, Any]:
    require_role(conn, user_id, WRITE_ROLES)
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO activity_resource_dependencies (activity_id, resource_id, dependency_type, minimum_quantity_required, notes)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING *
            """,
            (str(activity_id), str(resource_id), dependency_type, minimum_quantity_required, notes),
        )
        row = cur.fetchone()
    log_audit(conn, user_id, "CREATE", "activity_resource_dependencies", row["dependency_id"], {"activity_id": str(activity_id), "resource_id": str(resource_id)})
    return row


def get_activity_resource_dependencies(conn: psycopg.Connection, activity_id: UUID | str) -> list[dict[str, Any]]:
    """Returns all resource dependencies for an activity, joined with resource details."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT d.*, r.name AS resource_name, r.resource_type, r.is_single_point_of_failure
            FROM activity_resource_dependencies d
            JOIN resources r ON r.resource_id = d.resource_id
            WHERE d.activity_id = %s
            ORDER BY r.name
            """,
            (str(activity_id),),
        )
        return cur.fetchall()


# ---------------------------------------------------------------------------
# Impact matrix configuration
# ---------------------------------------------------------------------------


def create_impact_category(
    conn: psycopg.Connection,
    user_id: UUID | str,
    organization_id: UUID | str,
    category_type: str,
    name: str,
    description: Optional[str] = None,
) -> dict[str, Any]:
    require_role(conn, user_id, WRITE_ROLES)
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO impact_categories (organization_id, category_type, name, description)
            VALUES (%s, %s, %s, %s)
            RETURNING *
            """,
            (str(organization_id), category_type, name, description),
        )
        row = cur.fetchone()
    log_audit(conn, user_id, "CREATE", "impact_categories", row["category_id"], {"category_type": category_type, "name": name}, organization_id=organization_id)
    return row


def list_impact_categories(conn: psycopg.Connection, organization_id: UUID | str) -> list[dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM impact_categories WHERE organization_id = %s ORDER BY category_type", (str(organization_id),)
        )
        return cur.fetchall()


def create_impact_threshold(
    conn: psycopg.Connection,
    user_id: UUID | str,
    category_id: UUID | str,
    severity: str,
    time_timeframe_hours: int,
    qualitative_criteria: str,
    financial_cost_min: Optional[float] = None,
    financial_cost_max: Optional[float] = None,
) -> dict[str, Any]:
    require_role(conn, user_id, WRITE_ROLES)
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO impact_thresholds
                (category_id, severity, time_timeframe_hours, financial_cost_min, financial_cost_max, qualitative_criteria)
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING *
            """,
            (str(category_id), severity, time_timeframe_hours, financial_cost_min, financial_cost_max, qualitative_criteria),
        )
        row = cur.fetchone()
    log_audit(conn, user_id, "CREATE", "impact_thresholds", row["threshold_id"], {"severity": severity, "time_timeframe_hours": time_timeframe_hours})
    return row


def list_impact_thresholds(conn: psycopg.Connection, category_id: UUID | str) -> list[dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM impact_thresholds WHERE category_id = %s ORDER BY time_timeframe_hours", (str(category_id),)
        )
        return cur.fetchall()


# ---------------------------------------------------------------------------
# MTPD / RTO / RPO / MBCO capture (bia_assessments)
# ---------------------------------------------------------------------------


def _check_rto_less_than_mtpd(mtpd_hours: int, rto_hours: int) -> None:
    """Fast, friendly pre-check mirroring the DB's chk_rto_less_than_mtpd
    CHECK constraint, so callers get an immediate, clear error without a
    round-trip to Postgres. The DB constraint remains the authoritative
    enforcement point (see _translate_db_error).
    """
    if rto_hours >= mtpd_hours:
        raise RTOConstraintViolation(
            f"Invalid BIA parameters: RTO ({rto_hours}h) must be strictly less than "
            f"MTPD ({mtpd_hours}h). The Recovery Time Objective cannot equal or exceed "
            "the Maximum Tolerable Period of Disruption."
        )


def _translate_db_error(exc: Exception, mtpd_hours: Optional[int] = None, rto_hours: Optional[int] = None) -> None:
    """Re-raises known Postgres constraint violations as clear BCMPlannerError
    subclasses instead of letting a raw psycopg traceback surface to callers.
    """
    if isinstance(exc, psycopg.errors.CheckViolation):
        diag = getattr(exc, "diag", None)
        constraint = getattr(diag, "constraint_name", "") if diag else ""
        if constraint == "chk_rto_less_than_mtpd":
            detail = ""
            if mtpd_hours is not None and rto_hours is not None:
                detail = f" (submitted RTO={rto_hours}h, MTPD={mtpd_hours}h)"
            raise RTOConstraintViolation(
                "Database rejected this BIA assessment: RTO must be strictly less than "
                f"MTPD (constraint chk_rto_less_than_mtpd){detail}."
            ) from exc
        raise BCMPlannerError(f"Database rejected the operation due to a data constraint: {exc}") from exc
    raise


def create_bia_assessment(
    conn: psycopg.Connection,
    user_id: UUID | str,
    activity_id: UUID | str,
    assessor_name: str,
    assessment_date: datetime.date,
    mtpd_hours: int,
    rto_hours: int,
    rpo_hours: Optional[int] = None,
    mbco_percentage: float = 100.00,
) -> dict[str, Any]:
    """Creates a bia_assessments row. Enforces RTO < MTPD via a fast Python
    pre-check first, then relies on the DB CHECK constraint as the
    authoritative backstop (translated into a clear error if it fires).
    """
    require_role(conn, user_id, WRITE_ROLES)
    _check_rto_less_than_mtpd(mtpd_hours, rto_hours)

    savepoint = "sp_create_bia"
    with conn.cursor() as cur:
        cur.execute(f"SAVEPOINT {savepoint}")
        try:
            cur.execute(
                """
                INSERT INTO bia_assessments
                    (activity_id, assessor_name, assessment_date, mtpd_hours, rto_hours, rpo_hours, mbco_percentage)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                RETURNING *
                """,
                (str(activity_id), assessor_name, assessment_date, mtpd_hours, rto_hours, rpo_hours, mbco_percentage),
            )
            row = cur.fetchone()
        except psycopg.errors.CheckViolation as exc:
            cur.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
            _translate_db_error(exc, mtpd_hours=mtpd_hours, rto_hours=rto_hours)
        else:
            cur.execute(f"RELEASE SAVEPOINT {savepoint}")

    log_audit(
        conn, user_id, "CREATE", "bia_assessments", row["bia_id"],
        {"activity_id": str(activity_id), "mtpd_hours": mtpd_hours, "rto_hours": rto_hours, "rpo_hours": rpo_hours},
    )
    return row


def update_bia_assessment(
    conn: psycopg.Connection,
    user_id: UUID | str,
    bia_id: UUID | str,
    **fields: Any,
) -> dict[str, Any]:
    """Updates a subset of mutable bia_assessments fields.

    Allowed fields: assessor_name, assessment_date, mtpd_hours, rto_hours,
    rpo_hours, mbco_percentage, status, approved_by, approval_date.
    If mtpd_hours and/or rto_hours are being changed, the resulting pair is
    pre-checked against the RTO < MTPD rule before hitting the DB.
    """
    require_role(conn, user_id, WRITE_ROLES)
    allowed_fields = {
        "assessor_name", "assessment_date", "mtpd_hours", "rto_hours",
        "rpo_hours", "mbco_percentage", "status", "approved_by", "approval_date",
    }
    unknown = set(fields) - allowed_fields
    if unknown:
        raise BCMPlannerError(f"Unknown/immutable bia_assessments field(s): {', '.join(sorted(unknown))}")
    if not fields:
        raise BCMPlannerError("update_bia_assessment called with no fields to update.")

    current = get_bia_assessment(conn, bia_id)
    new_mtpd = fields.get("mtpd_hours", current["mtpd_hours"])
    new_rto = fields.get("rto_hours", current["rto_hours"])
    if "mtpd_hours" in fields or "rto_hours" in fields:
        _check_rto_less_than_mtpd(new_mtpd, new_rto)

    set_clause = ", ".join(f"{k} = %s" for k in fields)
    params = list(fields.values()) + [str(bia_id)]

    savepoint = "sp_update_bia"
    with conn.cursor() as cur:
        cur.execute(f"SAVEPOINT {savepoint}")
        try:
            cur.execute(
                f"UPDATE bia_assessments SET {set_clause} WHERE bia_id = %s RETURNING *",
                params,
            )
            row = cur.fetchone()
        except psycopg.errors.CheckViolation as exc:
            cur.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
            _translate_db_error(exc, mtpd_hours=new_mtpd, rto_hours=new_rto)
        else:
            cur.execute(f"RELEASE SAVEPOINT {savepoint}")

    if row is None:
        raise NotFoundError(f"BIA assessment {bia_id} not found.")

    log_audit(conn, user_id, "UPDATE", "bia_assessments", bia_id, fields)
    return row


def get_bia_assessment(conn: psycopg.Connection, bia_id: UUID | str) -> dict[str, Any]:
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM bia_assessments WHERE bia_id = %s", (str(bia_id),))
        row = cur.fetchone()
    if row is None:
        raise NotFoundError(f"BIA assessment {bia_id} not found.")
    return row


def list_bia_assessments_by_activity(conn: psycopg.Connection, activity_id: UUID | str) -> list[dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM bia_assessments WHERE activity_id = %s ORDER BY assessment_date DESC", (str(activity_id),)
        )
        return cur.fetchall()


# ---------------------------------------------------------------------------
# Gap analysis
# ---------------------------------------------------------------------------


def create_gap_analysis(
    conn: psycopg.Connection,
    user_id: UUID | str,
    bia_id: UUID | str,
    current_recovery_capability_hours: int,
    target_rto_hours: int,
    risk_summary: str,
    identified_spof: Optional[str] = None,
) -> dict[str, Any]:
    """Creates a gap_analyses row. gap_hours is a DB GENERATED ALWAYS AS
    STORED column (current_recovery_capability_hours - target_rto_hours) —
    never written by the caller, always read back via RETURNING *.
    """
    require_role(conn, user_id, WRITE_ROLES)
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO gap_analyses
                (bia_id, current_recovery_capability_hours, target_rto_hours, identified_spof, risk_summary)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING *
            """,
            (str(bia_id), current_recovery_capability_hours, target_rto_hours, identified_spof, risk_summary),
        )
        row = cur.fetchone()
    log_audit(
        conn, user_id, "CREATE", "gap_analyses", row["gap_id"],
        {"bia_id": str(bia_id), "current_recovery_capability_hours": current_recovery_capability_hours, "target_rto_hours": target_rto_hours, "gap_hours": row["gap_hours"]},
    )
    return row


def get_gap_analysis(conn: psycopg.Connection, gap_id: UUID | str) -> dict[str, Any]:
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM gap_analyses WHERE gap_id = %s", (str(gap_id),))
        row = cur.fetchone()
    if row is None:
        raise NotFoundError(f"Gap analysis {gap_id} not found.")
    return row


def list_gap_analyses_by_bia(conn: psycopg.Connection, bia_id: UUID | str) -> list[dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM gap_analyses WHERE bia_id = %s", (str(bia_id),))
        return cur.fetchall()


def detect_single_points_of_failure(conn: psycopg.Connection, activity_id: UUID | str) -> list[dict[str, Any]]:
    """Cross-references resources.is_single_point_of_failure = true against
    activity_resource_dependencies for the given activity. Returns the
    dependency + resource rows flagged as SPOFs for that activity.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT d.dependency_id, d.activity_id, r.resource_id, r.name AS resource_name,
                   r.resource_type, d.dependency_type, d.minimum_quantity_required
            FROM activity_resource_dependencies d
            JOIN resources r ON r.resource_id = d.resource_id
            WHERE d.activity_id = %s AND r.is_single_point_of_failure = TRUE
            ORDER BY r.name
            """,
            (str(activity_id),),
        )
        return cur.fetchall()


# ---------------------------------------------------------------------------
# Recovery strategy selection
# ---------------------------------------------------------------------------


def create_recovery_strategy(
    conn: psycopg.Connection,
    user_id: UUID | str,
    bia_id: UUID | str,
    category: str,
    strategy_name: str,
    description: str,
    estimated_implementation_cost: Optional[float] = None,
    prerequisites: Optional[str] = None,
    is_selected_option: bool = False,
) -> dict[str, Any]:
    """Creates a recovery_strategies row. If is_selected_option=True, any
    previously-selected strategy for the same bia_id is un-marked first
    (there is no DB constraint enforcing "only one selected per bia_id" —
    this is handled here, in application logic, inside the same transaction).
    """
    require_role(conn, user_id, WRITE_ROLES)
    with conn.cursor() as cur:
        if is_selected_option:
            cur.execute(
                "UPDATE recovery_strategies SET is_selected_option = FALSE WHERE bia_id = %s AND is_selected_option = TRUE",
                (str(bia_id),),
            )
        cur.execute(
            """
            INSERT INTO recovery_strategies
                (bia_id, category, strategy_name, description, estimated_implementation_cost, is_selected_option, prerequisites)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            RETURNING *
            """,
            (str(bia_id), category, strategy_name, description, estimated_implementation_cost, is_selected_option, prerequisites),
        )
        row = cur.fetchone()
    log_audit(
        conn, user_id, "CREATE", "recovery_strategies", row["strategy_id"],
        {"bia_id": str(bia_id), "category": category, "is_selected_option": is_selected_option},
    )
    return row


def select_recovery_strategy(
    conn: psycopg.Connection,
    user_id: UUID | str,
    bia_id: UUID | str,
    strategy_id: UUID | str,
) -> dict[str, Any]:
    """Marks `strategy_id` as the selected option for `bia_id`, un-marking
    any previously-selected strategy for that same bia_id in the same
    transaction. Enforces "only one selected per bia_id" in application
    logic since the schema has no partial-unique-index for it.
    """
    require_role(conn, user_id, STRATEGY_SELECTION_ROLES)
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE recovery_strategies SET is_selected_option = FALSE WHERE bia_id = %s AND is_selected_option = TRUE AND strategy_id != %s",
            (str(bia_id), str(strategy_id)),
        )
        cur.execute(
            "UPDATE recovery_strategies SET is_selected_option = TRUE WHERE strategy_id = %s AND bia_id = %s RETURNING *",
            (str(strategy_id), str(bia_id)),
        )
        row = cur.fetchone()
    if row is None:
        raise NotFoundError(f"Recovery strategy {strategy_id} not found for bia_id {bia_id}.")
    log_audit(conn, user_id, "UPDATE", "recovery_strategies", strategy_id, {"is_selected_option": True, "bia_id": str(bia_id)})
    return row


def list_recovery_strategies_by_bia(conn: psycopg.Connection, bia_id: UUID | str) -> list[dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM recovery_strategies WHERE bia_id = %s ORDER BY strategy_name", (str(bia_id),))
        return cur.fetchall()
