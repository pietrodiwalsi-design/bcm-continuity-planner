"""Crisis Management Team (CMT) & Escalation Mapping — Phase 3 of the BCM
Continuity Planner.

Implements, against the PostgreSQL schema in schema/001_core_schema.sql
(the `cmt_roles` and `escalation_triggers` tables were already defined
there — no new migration was needed for this phase), covering FR9:

- CRUD for `cmt_roles`: role definitions for the Crisis Management Team
  (Crisis Management Team Leader, Legal Counsel, IT/Security Head,
  Spokesperson, etc.), scoped to `organization_id`.
- CRUD for `escalation_triggers`: severity-based escalation rules
  (`severity_level_enum`: minimal/minor/moderate/major/severe/
  catastrophic), scoped to `organization_id`.
- `get_escalation_path_for_severity`: given an organization + severity
  level, returns the matching escalation trigger(s) plus the CMT roles
  that should be notified for that severity (see the heuristic documented
  in detail below the function).

Conventions reused, unchanged, from bia_engine.py (Phase 1) / bcp_generator.py
(Phase 2) per the Phase 3 brief — this module does not reinvent RBAC, audit
logging, error handling, or connection style:

- psycopg (v3), dict-row cursors via bcm_planner.db.get_connection().
- `bia_engine.require_role` for RBAC (same WRITE_ROLES allow-list).
- `bia_engine.log_audit` for append-only audit logging.
- `bia_engine.BCMPlannerError` / `NotFoundError` / `InsufficientRoleError`
  as the shared exception hierarchy.
"""

from __future__ import annotations

import re
from typing import Any, Optional
from uuid import UUID

import psycopg

from bcm_planner.bia_engine import (
    BCMPlannerError,
    InsufficientRoleError,
    NotFoundError,
    WRITE_ROLES,
    require_role,
    log_audit,
)

__all__ = [
    "BCMPlannerError",
    "InsufficientRoleError",
    "NotFoundError",
    "create_cmt_role",
    "get_cmt_role",
    "update_cmt_role",
    "list_cmt_roles_by_organization",
    "create_escalation_trigger",
    "get_escalation_trigger",
    "update_escalation_trigger",
    "list_escalation_triggers_by_organization",
    "get_escalation_path_for_severity",
    "HIGH_SEVERITY_ALL_ROLES_LEVELS",
    "FIRST_RESPONSE_ROLE_KEYWORDS",
]


# ---------------------------------------------------------------------------
# cmt_roles CRUD
# ---------------------------------------------------------------------------


def create_cmt_role(
    conn: psycopg.Connection,
    user_id: UUID | str,
    organization_id: UUID | str,
    role_name: str,
    primary_assignee_name: str,
    primary_assignee_phone: str,
    primary_assignee_email: str,
    key_responsibilities: str,
    alternate_assignee_name: Optional[str] = None,
    alternate_assignee_phone: Optional[str] = None,
) -> dict[str, Any]:
    """Creates a cmt_roles row (e.g. Crisis Management Team Leader, Legal
    Counsel, IT/Security Head, Spokesperson) scoped to an organization.
    """
    require_role(conn, user_id, WRITE_ROLES)
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO cmt_roles
                (organization_id, role_name, primary_assignee_name, primary_assignee_phone,
                 primary_assignee_email, alternate_assignee_name, alternate_assignee_phone,
                 key_responsibilities)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING *
            """,
            (
                str(organization_id), role_name, primary_assignee_name, primary_assignee_phone,
                primary_assignee_email, alternate_assignee_name, alternate_assignee_phone,
                key_responsibilities,
            ),
        )
        row = cur.fetchone()
    log_audit(
        conn, user_id, "CREATE", "cmt_roles", row["role_id"],
        {"role_name": role_name, "primary_assignee_name": primary_assignee_name},
        organization_id=organization_id,
    )
    return row


def get_cmt_role(conn: psycopg.Connection, role_id: UUID | str) -> dict[str, Any]:
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM cmt_roles WHERE role_id = %s", (str(role_id),))
        row = cur.fetchone()
    if row is None:
        raise NotFoundError(f"CMT role {role_id} not found.")
    return row


def list_cmt_roles_by_organization(conn: psycopg.Connection, organization_id: UUID | str) -> list[dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM cmt_roles WHERE organization_id = %s ORDER BY role_name",
            (str(organization_id),),
        )
        return cur.fetchall()


def update_cmt_role(
    conn: psycopg.Connection,
    user_id: UUID | str,
    role_id: UUID | str,
    **fields: Any,
) -> dict[str, Any]:
    """Updates a subset of mutable cmt_roles fields.

    Allowed fields: role_name, primary_assignee_name, primary_assignee_phone,
    primary_assignee_email, alternate_assignee_name, alternate_assignee_phone,
    key_responsibilities.
    """
    require_role(conn, user_id, WRITE_ROLES)
    allowed_fields = {
        "role_name", "primary_assignee_name", "primary_assignee_phone",
        "primary_assignee_email", "alternate_assignee_name",
        "alternate_assignee_phone", "key_responsibilities",
    }
    unknown = set(fields) - allowed_fields
    if unknown:
        raise BCMPlannerError(f"Unknown/immutable cmt_roles field(s): {', '.join(sorted(unknown))}")
    if not fields:
        raise BCMPlannerError("update_cmt_role called with no fields to update.")

    set_clause = ", ".join(f"{k} = %s" for k in fields)
    params = list(fields.values()) + [str(role_id)]
    with conn.cursor() as cur:
        cur.execute(f"UPDATE cmt_roles SET {set_clause} WHERE role_id = %s RETURNING *", params)
        row = cur.fetchone()
    if row is None:
        raise NotFoundError(f"CMT role {role_id} not found.")
    log_audit(conn, user_id, "UPDATE", "cmt_roles", role_id, fields)
    return row


# ---------------------------------------------------------------------------
# escalation_triggers CRUD
# ---------------------------------------------------------------------------


def create_escalation_trigger(
    conn: psycopg.Connection,
    user_id: UUID | str,
    organization_id: UUID | str,
    severity_level: str,
    incident_condition: str,
    notification_timeframe_minutes: int,
    required_action: str,
) -> dict[str, Any]:
    """Creates an escalation_triggers row. `severity_level` must be one of
    severity_level_enum's values (minimal/minor/moderate/major/severe/
    catastrophic) — the DB rejects invalid values with a clear error.
    """
    require_role(conn, user_id, WRITE_ROLES)
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO escalation_triggers
                (organization_id, severity_level, incident_condition,
                 notification_timeframe_minutes, required_action)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING *
            """,
            (str(organization_id), severity_level, incident_condition, notification_timeframe_minutes, required_action),
        )
        row = cur.fetchone()
    log_audit(
        conn, user_id, "CREATE", "escalation_triggers", row["trigger_id"],
        {"severity_level": severity_level, "notification_timeframe_minutes": notification_timeframe_minutes},
        organization_id=organization_id,
    )
    return row


def get_escalation_trigger(conn: psycopg.Connection, trigger_id: UUID | str) -> dict[str, Any]:
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM escalation_triggers WHERE trigger_id = %s", (str(trigger_id),))
        row = cur.fetchone()
    if row is None:
        raise NotFoundError(f"Escalation trigger {trigger_id} not found.")
    return row


def list_escalation_triggers_by_organization(conn: psycopg.Connection, organization_id: UUID | str) -> list[dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM escalation_triggers WHERE organization_id = %s ORDER BY severity_level",
            (str(organization_id),),
        )
        return cur.fetchall()


def update_escalation_trigger(
    conn: psycopg.Connection,
    user_id: UUID | str,
    trigger_id: UUID | str,
    **fields: Any,
) -> dict[str, Any]:
    """Updates a subset of mutable escalation_triggers fields.

    Allowed fields: severity_level, incident_condition,
    notification_timeframe_minutes, required_action.
    """
    require_role(conn, user_id, WRITE_ROLES)
    allowed_fields = {
        "severity_level", "incident_condition",
        "notification_timeframe_minutes", "required_action",
    }
    unknown = set(fields) - allowed_fields
    if unknown:
        raise BCMPlannerError(f"Unknown/immutable escalation_triggers field(s): {', '.join(sorted(unknown))}")
    if not fields:
        raise BCMPlannerError("update_escalation_trigger called with no fields to update.")

    set_clause = ", ".join(f"{k} = %s" for k in fields)
    params = list(fields.values()) + [str(trigger_id)]
    with conn.cursor() as cur:
        cur.execute(f"UPDATE escalation_triggers SET {set_clause} WHERE trigger_id = %s RETURNING *", params)
        row = cur.fetchone()
    if row is None:
        raise NotFoundError(f"Escalation trigger {trigger_id} not found.")
    log_audit(conn, user_id, "UPDATE", "escalation_triggers", trigger_id, fields)
    return row


# ---------------------------------------------------------------------------
# Escalation path helper (FR9)
# ---------------------------------------------------------------------------

# Severity levels for which ALL CMT roles for the organization are notified
# (a major/severe/catastrophic incident is assumed to require the full
# Crisis Management Team, not just first-responders — this is the simple,
# documented heuristic requested by the Phase 3 brief; it is deliberately
# not sophisticated).
HIGH_SEVERITY_ALL_ROLES_LEVELS: frozenset[str] = frozenset({"major", "severe", "catastrophic"})

# For lower severities (minimal/minor/moderate), only roles whose
# role_name or key_responsibilities text suggests operational/first-response
# involvement are notified — e.g. "IT/Security Head", "Operations Lead",
# "Duty Manager" rather than "Legal Counsel" or "Spokesperson", who are
# reserved for higher-severity incidents. This is a plain keyword match
# against role_name + key_responsibilities (case-insensitive substring
# match), not NLP/ML — simple and auditable, per the Phase 3 brief.
FIRST_RESPONSE_ROLE_KEYWORDS: frozenset[str] = frozenset({
    "operations", "operational", "it", "security", "technical", "network",
    "infrastructure", "duty manager", "first responder", "incident manager",
    "facilities", "engineering",
})


def _is_first_response_role(role: dict[str, Any]) -> bool:
    """Applies the FIRST_RESPONSE_ROLE_KEYWORDS heuristic against a
    cmt_roles row's role_name + key_responsibilities (lower-cased,
    word-boundary regex match — NOT naive substring match, since short
    keywords like "it" would otherwise false-positive inside unrelated
    words such as "author-it-y" or "capac-it-y"). Documented in full above
    HIGH_SEVERITY_ALL_ROLES_LEVELS.
    """
    haystack = f"{role.get('role_name', '')} {role.get('key_responsibilities', '')}".lower()
    return any(
        re.search(r"\b" + re.escape(keyword) + r"\b", haystack)
        for keyword in FIRST_RESPONSE_ROLE_KEYWORDS
    )


def get_escalation_path_for_severity(
    conn: psycopg.Connection,
    organization_id: UUID | str,
    severity_level: str,
) -> dict[str, Any]:
    """Returns the escalation path for a given organization + severity
    level: the matching escalation_triggers row(s) for that severity, plus
    the CMT roles that should be notified.

    Heuristic (documented in code above, see HIGH_SEVERITY_ALL_ROLES_LEVELS
    and FIRST_RESPONSE_ROLE_KEYWORDS):
    - major / severe / catastrophic -> ALL cmt_roles for the organization
      are notified (a full-severity incident needs the whole CMT).
    - minimal / minor / moderate -> only cmt_roles whose role_name or
      key_responsibilities suggest operational/first-response involvement
      are notified (e.g. IT/Security Head, Operations Lead) — roles like
      Legal Counsel or Spokesperson are reserved for higher severities in
      this simple model.

    This is intentionally a simple, sensible default, not a sophisticated
    notification-matrix engine — per the Phase 3 brief.

    Returns {"severity_level": ..., "triggers": [...], "notified_roles": [...]}.
    Read-only — no RBAC/audit-log write since nothing is created here.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM escalation_triggers WHERE organization_id = %s AND severity_level = %s ORDER BY notification_timeframe_minutes",
            (str(organization_id), severity_level),
        )
        triggers = cur.fetchall()

    all_roles = list_cmt_roles_by_organization(conn, organization_id)

    if severity_level in HIGH_SEVERITY_ALL_ROLES_LEVELS:
        notified_roles = all_roles
    else:
        notified_roles = [r for r in all_roles if _is_first_response_role(r)]

    return {
        "severity_level": severity_level,
        "triggers": triggers,
        "notified_roles": notified_roles,
    }
