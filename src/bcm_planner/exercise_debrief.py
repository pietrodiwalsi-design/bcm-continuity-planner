"""Debrief, Hot-Debrief & Action Tracking — Phase 4 of the BCM Continuity
Planner.

Implements, against the PostgreSQL schema in schema/001_core_schema.sql
(the `exercise_debriefs` and `capa_action_items` tables were already
defined there — no new migration was needed for this phase), covering
FR13:

- CRUD for `exercise_debriefs`: hot-debrief summary + strengths/
  weaknesses/opportunities/threats + overall_rating, one per exercise_id
  (the schema enforces `UNIQUE(exercise_id)`; this module translates a
  second-debrief attempt into a clear `DuplicateDebriefError` rather than
  a raw `psycopg.errors.UniqueViolation` traceback).
- CRUD for `capa_action_items`: Corrective/Preventive Action items (gap
  description, corrective action required, assigned owner, due date,
  status, completion date), linked to a `debrief_id`.
- `get_overdue_capa_items(organization_id)`: a read-only helper surfacing
  CAPA items past their `due_date` and not yet completed, across every
  exercise/debrief for an organization — the "does the tool actually
  track follow-through" demo feature called out in the Phase 4 brief.

Conventions reused, unchanged, from bia_engine.py (Phase 1) /
bcp_generator.py (Phase 2) / crisis_management.py + crisis_communications.py
(Phase 3) / exercise_planner.py + scenario_injects.py (this phase) per the
Phase 4 brief — this module does not reinvent RBAC, audit logging, error
handling, or connection style:

- psycopg (v3), dict-row cursors via bcm_planner.db.get_connection().
- `bia_engine.require_role` for RBAC (same WRITE_ROLES allow-list).
- `bia_engine.log_audit` for append-only audit logging.
- `bia_engine.BCMPlannerError` / `NotFoundError` / `InsufficientRoleError`
  as the shared exception hierarchy (this module adds one new leaf
  exception, `DuplicateDebriefError`).
- The same SAVEPOINT + DB-constraint-translation pattern used for
  `chk_rto_less_than_mtpd` in bia_engine.py / `step_number > 0` in
  bcp_generator.py, applied here to exercise_debriefs'
  `UNIQUE(exercise_id)` constraint.
"""

from __future__ import annotations

import datetime
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
    "DuplicateDebriefError",
    "create_exercise_debrief",
    "get_exercise_debrief",
    "get_exercise_debrief_by_exercise",
    "update_exercise_debrief",
    "create_capa_action_item",
    "get_capa_action_item",
    "update_capa_action_item",
    "list_capa_action_items_by_debrief",
    "get_overdue_capa_items",
    "get_open_capa_items",
]


class DuplicateDebriefError(BCMPlannerError):
    """Raised when create_exercise_debrief is called for an exercise_id
    that already has a debrief row (exercise_debriefs.exercise_id UNIQUE
    constraint), whether caught by the Python pre-check or translated
    from the database's UniqueViolation — same pattern as bia_engine's
    RTOConstraintViolation / bcp_generator's InvalidStepNumberError.
    """


# ---------------------------------------------------------------------------
# exercise_debriefs CRUD
# ---------------------------------------------------------------------------


def get_exercise_debrief_by_exercise(conn: psycopg.Connection, exercise_id: UUID | str) -> Optional[dict[str, Any]]:
    """Reads the exercise_debriefs row for an exercise_id, if one exists.
    Returns None (not NotFoundError) since "no debrief yet" is a normal,
    expected state for a scheduled/in-progress exercise — used both as a
    public read helper and internally by create_exercise_debrief's
    pre-check.
    """
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM exercise_debriefs WHERE exercise_id = %s", (str(exercise_id),))
        return cur.fetchone()


def create_exercise_debrief(
    conn: psycopg.Connection,
    user_id: UUID | str,
    exercise_id: UUID | str,
    hot_debrief_summary: str,
    strengths_observed: Optional[str] = None,
    weaknesses_observed: Optional[str] = None,
    opportunities_for_improvement: Optional[str] = None,
    identified_threats_risks: Optional[str] = None,
    overall_rating: Optional[str] = None,
) -> dict[str, Any]:
    """Creates an exercise_debriefs row for an exercise. Enforces "one
    debrief per exercise_id" via a fast Python pre-check first, then
    relies on the DB UNIQUE(exercise_id) constraint as the authoritative
    backstop (translated into a clear DuplicateDebriefError, not a raw
    psycopg traceback, if it somehow still fires — e.g. a race between
    the pre-check and the insert).
    """
    require_role(conn, user_id, WRITE_ROLES)

    existing = get_exercise_debrief_by_exercise(conn, exercise_id)
    if existing is not None:
        raise DuplicateDebriefError(
            f"Exercise {exercise_id} already has a debrief (debrief_id={existing['debrief_id']}). "
            "Only one exercise_debriefs row is permitted per exercise — use "
            "update_exercise_debrief(...) to amend the existing debrief instead of creating a "
            "second one."
        )

    savepoint = "sp_create_exercise_debrief"
    with conn.cursor() as cur:
        cur.execute(f"SAVEPOINT {savepoint}")
        try:
            cur.execute(
                """
                INSERT INTO exercise_debriefs
                    (exercise_id, hot_debrief_summary, strengths_observed, weaknesses_observed,
                     opportunities_for_improvement, identified_threats_risks, overall_rating)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                RETURNING *
                """,
                (
                    str(exercise_id), hot_debrief_summary, strengths_observed, weaknesses_observed,
                    opportunities_for_improvement, identified_threats_risks, overall_rating,
                ),
            )
            row = cur.fetchone()
        except psycopg.errors.UniqueViolation as exc:
            cur.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
            raise DuplicateDebriefError(
                f"Database rejected this debrief: exercise {exercise_id} already has a debrief "
                "(UNIQUE(exercise_id) constraint). Use update_exercise_debrief(...) instead."
            ) from exc
        else:
            cur.execute(f"RELEASE SAVEPOINT {savepoint}")

    log_audit(
        conn, user_id, "CREATE", "exercise_debriefs", row["debrief_id"],
        {"exercise_id": str(exercise_id), "overall_rating": overall_rating},
    )
    return row


def get_exercise_debrief(conn: psycopg.Connection, debrief_id: UUID | str) -> dict[str, Any]:
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM exercise_debriefs WHERE debrief_id = %s", (str(debrief_id),))
        row = cur.fetchone()
    if row is None:
        raise NotFoundError(f"Exercise debrief {debrief_id} not found.")
    return row


def update_exercise_debrief(
    conn: psycopg.Connection,
    user_id: UUID | str,
    debrief_id: UUID | str,
    **fields: Any,
) -> dict[str, Any]:
    """Updates a subset of mutable exercise_debriefs fields.

    Allowed fields: hot_debrief_summary, strengths_observed,
    weaknesses_observed, opportunities_for_improvement,
    identified_threats_risks, overall_rating. exercise_id is intentionally
    NOT updatable here (re-pointing a debrief to a different exercise
    would bypass the one-debrief-per-exercise intent) — create a new
    debrief for a different exercise instead.
    """
    require_role(conn, user_id, WRITE_ROLES)
    allowed_fields = {
        "hot_debrief_summary", "strengths_observed", "weaknesses_observed",
        "opportunities_for_improvement", "identified_threats_risks", "overall_rating",
    }
    unknown = set(fields) - allowed_fields
    if unknown:
        raise BCMPlannerError(f"Unknown/immutable exercise_debriefs field(s): {', '.join(sorted(unknown))}")
    if not fields:
        raise BCMPlannerError("update_exercise_debrief called with no fields to update.")

    set_clause = ", ".join(f"{k} = %s" for k in fields)
    params = list(fields.values()) + [str(debrief_id)]
    with conn.cursor() as cur:
        cur.execute(f"UPDATE exercise_debriefs SET {set_clause} WHERE debrief_id = %s RETURNING *", params)
        row = cur.fetchone()
    if row is None:
        raise NotFoundError(f"Exercise debrief {debrief_id} not found.")
    log_audit(conn, user_id, "UPDATE", "exercise_debriefs", debrief_id, fields)
    return row


# ---------------------------------------------------------------------------
# capa_action_items CRUD
# ---------------------------------------------------------------------------


def create_capa_action_item(
    conn: psycopg.Connection,
    user_id: UUID | str,
    debrief_id: UUID | str,
    gap_description: str,
    corrective_action_required: str,
    assigned_owner: str,
    due_date: datetime.date,
    status: str = "Open",
    completion_date: Optional[datetime.date] = None,
) -> dict[str, Any]:
    """Creates a capa_action_items row under an exercise debrief.
    Validates debrief_id exists first so callers get a NotFoundError
    rather than a raw FK violation.
    """
    require_role(conn, user_id, WRITE_ROLES)
    get_exercise_debrief(conn, debrief_id)  # raises NotFoundError if missing
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO capa_action_items
                (debrief_id, gap_description, corrective_action_required, assigned_owner,
                 due_date, status, completion_date)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            RETURNING *
            """,
            (str(debrief_id), gap_description, corrective_action_required, assigned_owner, due_date, status, completion_date),
        )
        row = cur.fetchone()
    log_audit(
        conn, user_id, "CREATE", "capa_action_items", row["action_id"],
        {"debrief_id": str(debrief_id), "assigned_owner": assigned_owner, "due_date": str(due_date), "status": status},
    )
    return row


def get_capa_action_item(conn: psycopg.Connection, action_id: UUID | str) -> dict[str, Any]:
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM capa_action_items WHERE action_id = %s", (str(action_id),))
        row = cur.fetchone()
    if row is None:
        raise NotFoundError(f"CAPA action item {action_id} not found.")
    return row


def list_capa_action_items_by_debrief(conn: psycopg.Connection, debrief_id: UUID | str) -> list[dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM capa_action_items WHERE debrief_id = %s ORDER BY due_date",
            (str(debrief_id),),
        )
        return cur.fetchall()


def update_capa_action_item(
    conn: psycopg.Connection,
    user_id: UUID | str,
    action_id: UUID | str,
    **fields: Any,
) -> dict[str, Any]:
    """Updates a subset of mutable capa_action_items fields.

    Allowed fields: gap_description, corrective_action_required,
    assigned_owner, due_date, status, completion_date.
    """
    require_role(conn, user_id, WRITE_ROLES)
    allowed_fields = {
        "gap_description", "corrective_action_required", "assigned_owner",
        "due_date", "status", "completion_date",
    }
    unknown = set(fields) - allowed_fields
    if unknown:
        raise BCMPlannerError(f"Unknown/immutable capa_action_items field(s): {', '.join(sorted(unknown))}")
    if not fields:
        raise BCMPlannerError("update_capa_action_item called with no fields to update.")

    set_clause = ", ".join(f"{k} = %s" for k in fields)
    params = list(fields.values()) + [str(action_id)]
    with conn.cursor() as cur:
        cur.execute(f"UPDATE capa_action_items SET {set_clause} WHERE action_id = %s RETURNING *", params)
        row = cur.fetchone()
    if row is None:
        raise NotFoundError(f"CAPA action item {action_id} not found.")
    log_audit(conn, user_id, "UPDATE", "capa_action_items", action_id, fields)
    return row


# ---------------------------------------------------------------------------
# CAPA follow-through helper (FR13)
# ---------------------------------------------------------------------------

# Statuses treated as "not yet completed" for overdue/open detection.
# capa_action_items.status is a free-text VARCHAR (not an enum) with a
# documented default set of Open/In Progress/Completed/Verified (see
# schema/001_core_schema.sql comment on capa_action_items.status) — this
# module treats "Completed" and "Verified" as done, everything else
# (including any custom status text a caller might use) as still open.
_COMPLETED_STATUSES = frozenset({"completed", "verified"})


def get_overdue_capa_items(conn: psycopg.Connection, organization_id: UUID | str) -> list[dict[str, Any]]:
    """Returns all capa_action_items for an organization that are past
    their due_date (due_date < CURRENT_DATE) and not yet completed
    (status not in {'Completed', 'Verified'}, case-insensitive) —
    demo/dashboard feature showing the tool tracks CAPA follow-through,
    not just planning.

    Joins capa_action_items -> exercise_debriefs -> exercises ->
    exercise_programmes to scope by organization_id (capa_action_items
    does not carry organization_id directly). Ordered by due_date
    ascending (most overdue first).
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT c.*, e.exercise_id, e.title AS exercise_title, ep.organization_id
            FROM capa_action_items c
            JOIN exercise_debriefs d ON d.debrief_id = c.debrief_id
            JOIN exercises e ON e.exercise_id = d.exercise_id
            JOIN exercise_programmes ep ON ep.programme_id = e.programme_id
            WHERE ep.organization_id = %s
              AND c.due_date < CURRENT_DATE
              AND LOWER(c.status) != ALL(%s)
            ORDER BY c.due_date
            """,
            (str(organization_id), list(_COMPLETED_STATUSES)),
        )
        return cur.fetchall()


def get_open_capa_items(conn: psycopg.Connection, organization_id: UUID | str) -> list[dict[str, Any]]:
    """Returns all capa_action_items for an organization not yet completed
    (status not in {'Completed', 'Verified'}, case-insensitive),
    regardless of due_date — i.e. the full open/in-progress backlog,
    including items not yet overdue. See get_overdue_capa_items for the
    stricter "past due_date" subset. Same join path and ordering as
    get_overdue_capa_items.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT c.*, e.exercise_id, e.title AS exercise_title, ep.organization_id
            FROM capa_action_items c
            JOIN exercise_debriefs d ON d.debrief_id = c.debrief_id
            JOIN exercises e ON e.exercise_id = d.exercise_id
            JOIN exercise_programmes ep ON ep.programme_id = e.programme_id
            WHERE ep.organization_id = %s
              AND LOWER(c.status) != ALL(%s)
            ORDER BY c.due_date
            """,
            (str(organization_id), list(_COMPLETED_STATUSES)),
        )
        return cur.fetchall()
