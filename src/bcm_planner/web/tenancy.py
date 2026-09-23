"""Tenant isolation enforcement for the public web workshop tool.

Core requirement (see docs/web_workshop_tool.md "Tenant Isolation Model"):
every workshop/client is exactly one `organizations` row, and every route
in `bcm_planner.web.app` MUST filter by the current session's
`organization_id` before returning or mutating any entity. This module is
the single place that implements that filtering, so `app.py`'s routes
never write ad-hoc "does this belong to my org?" checks themselves — they
call one of the `get_*_scoped` / `require_*_scoped` functions below, all
of which raise `TenantMismatchError` (mapped by `app.py` to a plain HTTP
404, indistinguishable from "not found" so as not to leak cross-tenant
existence information) whenever the requested entity does not belong to
the resolved `organization_id`.

Every entity in the schema chains back to `organizations.organization_id`
(verified against schema/001_core_schema.sql / 002 / 003):
- `activities` -> `business_processes` -> `business_units` ->
  `organizations` (no direct FK on `activities`/`business_processes`, only
  on `business_units`).
- `bia_assessments` -> `activities` -> ... -> `organizations` (no direct
  FK on `bia_assessments`, only reachable via its activity).
- `recovery_strategies` -> `bia_assessments` -> ... -> `organizations`.
- `bc_plans`, `cmt_roles`, `escalation_triggers`, `resources`,
  `impact_categories` all carry `organization_id` directly.
- `bcp_action_steps` / `bau_return_procedures` -> `bc_plans` ->
  `organization_id` directly.

This module resolves the join chain once per entity type and returns the
resolved `organization_id` alongside the row, so callers get a single,
auditable point of truth for "which org does this belong to" rather than
re-deriving it inline in route handlers.
"""

from __future__ import annotations

import secrets
from typing import Any, Optional
from uuid import UUID

import psycopg

from bcm_planner.bia_engine import BCMPlannerError, NotFoundError

__all__ = [
    "TenantMismatchError",
    "SessionNotFoundError",
    "resolve_session",
    "get_activity_scoped",
    "get_business_process_scoped",
    "get_bia_assessment_scoped",
    "get_recovery_strategy_scoped",
    "get_bc_plan_scoped",
    "get_cmt_role_scoped",
    "get_escalation_trigger_scoped",
    "generate_session_token",
]


class TenantMismatchError(BCMPlannerError):
    """Raised whenever a requested entity exists but does not belong to
    the resolved session's organization_id. Always mapped to a plain
    HTTP 404 by app.py — never a 403 — so a workshop client cannot even
    learn (by response code) that a differently-scoped entity_id exists.
    """


class SessionNotFoundError(BCMPlannerError):
    """Raised when a `/session/<token>/...` URL's token does not match any
    `web_workshop_sessions` row (unknown, mistyped, or revoked token).
    """


def generate_session_token() -> str:
    """URL-safe random access token for a new workshop session. 32 bytes
    of entropy (43 base64url chars) — short enough for a clean workshop
    URL/link Peter can read aloud or paste during a live session, long
    enough that guessing another workshop's token is infeasible.
    """
    return secrets.token_urlsafe(32)


def resolve_session(conn: psycopg.Connection, session_token: str) -> dict[str, Any]:
    """Resolves a `/session/<token>/...` URL token to its
    `web_workshop_sessions` row (organization_id, admin_user_id,
    workshop_label). This is the ONLY entry point into a specific
    workshop's data — every route handler in app.py calls this first and
    threads the resulting `organization_id` through every subsequent
    `get_*_scoped` call for that request.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM web_workshop_sessions WHERE session_token = %s",
            (session_token,),
        )
        row = cur.fetchone()
    if row is None:
        raise SessionNotFoundError(f"No workshop session found for token {session_token!r}.")
    return row


# ---------------------------------------------------------------------------
# Per-entity-type organization resolution + scoping
# ---------------------------------------------------------------------------


def _resolve_organization_id_for_activity(conn: psycopg.Connection, activity_id: UUID | str) -> Optional[str]:
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
    return str(row["organization_id"]) if row else None


def _resolve_organization_id_for_process(conn: psycopg.Connection, process_id: UUID | str) -> Optional[str]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT bu.organization_id
            FROM business_processes bp
            JOIN business_units bu ON bu.unit_id = bp.unit_id
            WHERE bp.process_id = %s
            """,
            (str(process_id),),
        )
        row = cur.fetchone()
    return str(row["organization_id"]) if row else None


def get_activity_scoped(
    conn: psycopg.Connection, organization_id: UUID | str, activity_id: UUID | str
) -> dict[str, Any]:
    """Returns the `activities` row for `activity_id` only if it resolves
    (via business_processes -> business_units) to `organization_id`.
    """
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM activities WHERE activity_id = %s", (str(activity_id),))
        row = cur.fetchone()
    if row is None:
        raise NotFoundError(f"Activity {activity_id} not found.")
    owner_org = _resolve_organization_id_for_activity(conn, activity_id)
    if owner_org != str(organization_id):
        raise TenantMismatchError(
            f"Activity {activity_id} does not belong to organization {organization_id}."
        )
    return row


def get_business_process_scoped(
    conn: psycopg.Connection, organization_id: UUID | str, process_id: UUID | str
) -> dict[str, Any]:
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM business_processes WHERE process_id = %s", (str(process_id),))
        row = cur.fetchone()
    if row is None:
        raise NotFoundError(f"Business process {process_id} not found.")
    owner_org = _resolve_organization_id_for_process(conn, process_id)
    if owner_org != str(organization_id):
        raise TenantMismatchError(
            f"Business process {process_id} does not belong to organization {organization_id}."
        )
    return row


def get_bia_assessment_scoped(
    conn: psycopg.Connection, organization_id: UUID | str, bia_id: UUID | str
) -> dict[str, Any]:
    """bia_assessments has no direct organization_id column — resolved via
    its activity, same join chain as `get_activity_scoped`.
    """
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM bia_assessments WHERE bia_id = %s", (str(bia_id),))
        row = cur.fetchone()
    if row is None:
        raise NotFoundError(f"BIA assessment {bia_id} not found.")
    owner_org = _resolve_organization_id_for_activity(conn, row["activity_id"])
    if owner_org != str(organization_id):
        raise TenantMismatchError(
            f"BIA assessment {bia_id} does not belong to organization {organization_id}."
        )
    return row


def get_recovery_strategy_scoped(
    conn: psycopg.Connection, organization_id: UUID | str, strategy_id: UUID | str
) -> dict[str, Any]:
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM recovery_strategies WHERE strategy_id = %s", (str(strategy_id),))
        row = cur.fetchone()
    if row is None:
        raise NotFoundError(f"Recovery strategy {strategy_id} not found.")
    # recovery_strategies -> bia_assessments -> activities -> ... -> organizations
    bia = get_bia_assessment_scoped(conn, organization_id, row["bia_id"])  # raises if mismatched
    del bia
    return row


def get_bc_plan_scoped(
    conn: psycopg.Connection, organization_id: UUID | str, plan_id: UUID | str
) -> dict[str, Any]:
    """bc_plans carries organization_id directly — simplest scoping check
    in this module.
    """
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM bc_plans WHERE plan_id = %s", (str(plan_id),))
        row = cur.fetchone()
    if row is None:
        raise NotFoundError(f"BC plan {plan_id} not found.")
    if str(row["organization_id"]) != str(organization_id):
        raise TenantMismatchError(
            f"BC plan {plan_id} does not belong to organization {organization_id}."
        )
    return row


def get_cmt_role_scoped(
    conn: psycopg.Connection, organization_id: UUID | str, role_id: UUID | str
) -> dict[str, Any]:
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM cmt_roles WHERE role_id = %s", (str(role_id),))
        row = cur.fetchone()
    if row is None:
        raise NotFoundError(f"CMT role {role_id} not found.")
    if str(row["organization_id"]) != str(organization_id):
        raise TenantMismatchError(
            f"CMT role {role_id} does not belong to organization {organization_id}."
        )
    return row


def get_escalation_trigger_scoped(
    conn: psycopg.Connection, organization_id: UUID | str, trigger_id: UUID | str
) -> dict[str, Any]:
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM escalation_triggers WHERE trigger_id = %s", (str(trigger_id),))
        row = cur.fetchone()
    if row is None:
        raise NotFoundError(f"Escalation trigger {trigger_id} not found.")
    if str(row["organization_id"]) != str(organization_id):
        raise TenantMismatchError(
            f"Escalation trigger {trigger_id} does not belong to organization {organization_id}."
        )
    return row
