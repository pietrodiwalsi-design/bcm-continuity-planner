"""Governance & Lifecycle — Phase 5 of the BCM Continuity Planner.

Implements, against the PostgreSQL schema in
schema/002_rbac_approvals_review_additions.sql (`sign_off_approvals`,
`document_review_schedule`, `document_versions` — all already defined
there from the original schema review, see `schema/SCHEMA_REVIEW.md`)
plus one enum-widening addition in
schema/003_governance_test_report_entity_type.sql (`approval_entity_enum`
gains an `exercise_debrief` value so a Phase 4 test/exercise report can be
governed the same way as a BIA/BCP/strategy), covering FR14-FR15:

- **Multi-tier sign-off workflow (FR14)**: a lightweight, sequence-ordered
  state machine on top of `sign_off_approvals`. `create_sign_off_chain`
  creates one row per tier (all `pending`); `submit_sign_off_decision`
  records a tier's decision, but only after every earlier
  `sequence_order` tier for the same entity has already been decided
  `approved` — earlier tiers block later ones, exactly as FR14 requires
  ("process owner → top management"). `get_sign_off_status` is the
  read-only "what's the current approval status" helper.
- **Version control & review-cycle scheduler (FR15)**: CRUD for
  `document_review_schedule` plus `compute_next_review_date` (a simple,
  documented calendar-month heuristic — no external date library), and a
  generic `document_versions` maintenance/version log
  (`record_maintenance_update` / `list_document_versions_for_entity`)
  spanning every governed entity type (BIA assessment, BC plan, recovery
  strategy, crisis management plan, exercise debrief/"test report").

This module is deliberately a **rule-based state machine, not a workflow
engine** — same "deliberately simple, documented heuristic" pattern as
Phase 3's `crisis_management.get_escalation_path_for_severity` and Phase
4's `exercise_planner.DISRUPTION_SCENARIO_TEMPLATES`. See
`docs/governance_lifecycle.md` for the full state-machine and
review-cycle write-up.

Conventions reused, unchanged, from bia_engine.py (Phase 1) /
bcp_generator.py (Phase 2) / crisis_management.py + crisis_communications.py
(Phase 3) / exercise_planner.py + scenario_injects.py + exercise_debrief.py
(Phase 4) per the Phase 5 brief — this module does not reinvent RBAC,
audit logging, error handling, or connection style:

- psycopg (v3), dict-row cursors via bcm_planner.db.get_connection().
- `bia_engine.require_role` for RBAC. Structural CRUD (creating a
  sign-off chain, a review schedule, a version/maintenance log entry)
  uses the same `WRITE_ROLES` allow-list as every other phase. **Actually
  deciding a sign-off tier is different**: it requires the specific
  `approver_process_owner` / `approver_top_management` role recorded on
  that tier (or `admin`) — `approver_*` roles were deliberately excluded
  from `WRITE_ROLES` back in Phase 1 precisely so this distinction would
  exist (see the comment above `WRITE_ROLES` in `bia_engine.py`).
- `bia_engine.log_audit` for append-only audit logging.
- `bia_engine.BCMPlannerError` / `NotFoundError` / `InsufficientRoleError`
  as the shared exception hierarchy (this module adds
  `SignOffSequenceError`, `SignOffAlreadyDecidedError`,
  `DuplicateSignOffChainError`, `DuplicateVersionLabelError`).
"""

from __future__ import annotations

import calendar
import datetime
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
)

__all__ = [
    "BCMPlannerError",
    "InsufficientRoleError",
    "NotFoundError",
    "SignOffSequenceError",
    "SignOffAlreadyDecidedError",
    "DuplicateSignOffChainError",
    "DuplicateVersionLabelError",
    "APPROVAL_ENTITY_TYPES",
    "ENTITY_TABLE_MAP",
    "STANDARD_SIGN_OFF_TIERS",
    "create_sign_off_chain",
    "get_sign_off_approval",
    "list_sign_off_approvals_for_entity",
    "get_current_pending_tier",
    "get_sign_off_status",
    "submit_sign_off_decision",
    "restart_sign_off_chain",
    "compute_next_review_date",
    "create_review_schedule",
    "get_review_schedule",
    "get_review_schedule_for_entity",
    "update_review_schedule",
    "mark_review_completed",
    "list_upcoming_review_schedules",
    "get_overdue_review_schedules",
    "create_document_version",
    "get_document_version",
    "list_document_versions_for_entity",
    "get_latest_document_version",
    "record_maintenance_update",
]


class SignOffSequenceError(BCMPlannerError):
    """Raised when submit_sign_off_decision is called for a tier whose
    earlier sequence_order tier(s), for the same (entity_type, entity_id),
    have not yet all been decided 'approved' — the core "block progression
    until prior tier signs off" rule from the Phase 5 brief.
    """


class SignOffAlreadyDecidedError(BCMPlannerError):
    """Raised when submit_sign_off_decision targets a tier whose decision
    is already something other than 'pending'. A sign-off decision is
    treated as a one-shot signature, not something silently overwritable —
    see restart_sign_off_chain() for the documented, explicit way to send
    a chain back to all-pending for a resubmission cycle.
    """


class DuplicateSignOffChainError(BCMPlannerError):
    """Raised when create_sign_off_chain is called for an (entity_type,
    entity_id) pair that already has at least one sign_off_approvals row
    — same 'translate the DB constraint into a clear application error'
    pattern as Phase 4's DuplicateDebriefError.
    """


class DuplicateVersionLabelError(BCMPlannerError):
    """Raised when create_document_version is called with a version_label
    that already exists for the same (entity_type, entity_id) — translates
    document_versions' UNIQUE(entity_type, entity_id, version_label)
    constraint into a clear error, same pattern as DuplicateSignOffChainError.
    """


# ---------------------------------------------------------------------------
# Shared entity_type plumbing
# ---------------------------------------------------------------------------

# Every value approval_entity_enum currently supports (schema/002_... plus
# schema/003_governance_test_report_entity_type.sql's 'exercise_debrief'
# addition). Used to give a clear, immediate application-layer error for an
# unrecognized entity_type instead of letting an invalid-enum-value error
# surface from the database.
APPROVAL_ENTITY_TYPES: frozenset[str] = frozenset({
    "bia_assessment", "bc_plan", "recovery_strategy",
    "crisis_management_plan", "exercise_debrief",
})

# entity_type -> (backing table, primary key column), for the existence
# pre-check done by every function in this module that takes an
# (entity_type, entity_id) pair, mirroring the "validate the parent exists
# first so callers get a NotFoundError, not a raw FK/psycopg error" pattern
# used throughout Phases 1-4 (e.g. bcp_generator.create_exercise validating
# programme_id). 'crisis_management_plan' is deliberately absent: unlike
# the other four entity types, there is no single table representing "the"
# Crisis Management Plan as one row (a CMP is the combination of
# cmt_roles + escalation_triggers + stakeholder_contact_matrices +
# message_bank rows for an organization) — schema/002 nonetheless included
# it in approval_entity_enum for a future single-document CMP export/
# artifact. Existence-checking is simply skipped for that entity_type; see
# docs/governance_lifecycle.md section "entity_type without a backing table".
ENTITY_TABLE_MAP: dict[str, tuple[str, str]] = {
    "bia_assessment": ("bia_assessments", "bia_id"),
    "bc_plan": ("bc_plans", "plan_id"),
    "recovery_strategy": ("recovery_strategies", "strategy_id"),
    "exercise_debrief": ("exercise_debriefs", "debrief_id"),
}


def _validate_entity_type(entity_type: str) -> None:
    if entity_type not in APPROVAL_ENTITY_TYPES:
        known = ", ".join(sorted(APPROVAL_ENTITY_TYPES))
        raise BCMPlannerError(
            f"Unrecognized entity_type '{entity_type}'. Known entity types: {known}."
        )


def _validate_entity_exists(conn: psycopg.Connection, entity_type: str, entity_id: UUID | str) -> None:
    """Confirms the referenced row exists in its backing table, if that
    entity_type has one (see ENTITY_TABLE_MAP). No-op for entity_types
    without a single backing table (currently only 'crisis_management_plan').
    """
    mapping = ENTITY_TABLE_MAP.get(entity_type)
    if mapping is None:
        return
    table, pk_column = mapping
    with conn.cursor() as cur:
        cur.execute(f"SELECT 1 FROM {table} WHERE {pk_column} = %s", (str(entity_id),))  # nosec: table/pk_column from a fixed internal allow-list, never user input
        if cur.fetchone() is None:
            raise NotFoundError(f"{entity_type} {entity_id} not found (no matching row in {table}).")


# ---------------------------------------------------------------------------
# Multi-tier sign-off workflow (FR14)
# ---------------------------------------------------------------------------

# Default two-tier chain matching FR14's exact wording ("process owner →
# top management"). Callers may pass a custom `tiers` list to
# create_sign_off_chain instead (e.g. to add a third tier), but this is the
# sensible default for the common case and what the dashboard/docs assume
# unless told otherwise.
STANDARD_SIGN_OFF_TIERS: list[dict[str, Any]] = [
    {"sequence_order": 1, "required_role": "approver_process_owner"},
    {"sequence_order": 2, "required_role": "approver_top_management"},
]

_VALID_DECISIONS: frozenset[str] = frozenset({"approved", "rejected", "returned_for_revision"})


def create_sign_off_chain(
    conn: psycopg.Connection,
    user_id: UUID | str,
    entity_type: str,
    entity_id: UUID | str,
    tiers: Optional[list[dict[str, Any]]] = None,
) -> list[dict[str, Any]]:
    """Creates the full sign-off chain (one sign_off_approvals row per
    tier, all starting 'pending') for an entity. Defaults to
    STANDARD_SIGN_OFF_TIERS (process owner -> top management) if `tiers`
    is not given; each tier dict must have 'sequence_order' (int) and
    'required_role' (an app_role_enum value).

    Raises DuplicateSignOffChainError if a chain already exists for this
    (entity_type, entity_id) — use submit_sign_off_decision to act on an
    existing chain, or restart_sign_off_chain to reset one.
    """
    require_role(conn, user_id, WRITE_ROLES)
    _validate_entity_type(entity_type)
    _validate_entity_exists(conn, entity_type, entity_id)

    existing = list_sign_off_approvals_for_entity(conn, entity_type, entity_id)
    if existing:
        raise DuplicateSignOffChainError(
            f"A sign-off chain already exists for {entity_type} {entity_id} "
            f"({len(existing)} tier(s)). Use submit_sign_off_decision(...) to act on it, "
            "or restart_sign_off_chain(...) to reset it back to all-pending."
        )

    chain = tiers if tiers is not None else STANDARD_SIGN_OFF_TIERS
    if not chain:
        raise BCMPlannerError("create_sign_off_chain requires at least one tier.")

    savepoint = "sp_create_sign_off_chain"
    rows: list[dict[str, Any]] = []
    with conn.cursor() as cur:
        cur.execute(f"SAVEPOINT {savepoint}")
        try:
            for tier in chain:
                cur.execute(
                    """
                    INSERT INTO sign_off_approvals (entity_type, entity_id, sequence_order, required_role)
                    VALUES (%s, %s, %s, %s)
                    RETURNING *
                    """,
                    (entity_type, str(entity_id), tier["sequence_order"], tier["required_role"]),
                )
                rows.append(cur.fetchone())
        except psycopg.errors.UniqueViolation as exc:
            cur.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
            raise DuplicateSignOffChainError(
                f"Database rejected this chain: a sign-off tier already exists for {entity_type} "
                f"{entity_id} at one of the requested sequence_order values."
            ) from exc
        else:
            cur.execute(f"RELEASE SAVEPOINT {savepoint}")

    log_audit(
        conn, user_id, "CREATE", "sign_off_approvals", entity_id,
        {"entity_type": entity_type, "tier_count": len(rows),
         "required_roles": [t["required_role"] for t in chain]},
    )
    return rows


def get_sign_off_approval(conn: psycopg.Connection, approval_id: UUID | str) -> dict[str, Any]:
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM sign_off_approvals WHERE approval_id = %s", (str(approval_id),))
        row = cur.fetchone()
    if row is None:
        raise NotFoundError(f"Sign-off approval {approval_id} not found.")
    return row


def list_sign_off_approvals_for_entity(
    conn: psycopg.Connection, entity_type: str, entity_id: UUID | str
) -> list[dict[str, Any]]:
    """Returns every tier for an entity, ordered by sequence_order
    ascending (tier 1 first)."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM sign_off_approvals WHERE entity_type = %s AND entity_id = %s ORDER BY sequence_order",
            (entity_type, str(entity_id)),
        )
        return cur.fetchall()


def get_current_pending_tier(
    conn: psycopg.Connection, entity_type: str, entity_id: UUID | str
) -> Optional[dict[str, Any]]:
    """Returns the lowest-sequence_order tier still 'pending' for an
    entity (i.e. the next gate that needs to be actioned), or None if
    every tier has already been decided (whether all approved, or the
    chain stopped at a rejection/return-for-revision — see
    get_sign_off_status for the overall verdict, this helper only tells
    you which single tier is next).
    """
    tiers = list_sign_off_approvals_for_entity(conn, entity_type, entity_id)
    for tier in tiers:
        if tier["decision"] == "pending":
            return tier
    return None


def get_sign_off_status(
    conn: psycopg.Connection, entity_type: str, entity_id: UUID | str
) -> dict[str, Any]:
    """Read-only summary of an entity's sign-off state.

    overall_status (documented heuristic, deliberately simple):
    - 'not_started'          -- no sign_off_approvals rows exist yet.
    - 'rejected'              -- ANY tier's decision is 'rejected' (a
                                 rejection at any tier halts the chain
                                 outright, regardless of position).
    - 'returned_for_revision' -- no tier rejected, but ANY tier's decision
                                 is 'returned_for_revision' (also halts,
                                 pending a restart_sign_off_chain resubmission).
    - 'approved'              -- every tier's decision is 'approved'.
    - 'in_progress'           -- otherwise (at least one tier still 'pending',
                                 none rejected/returned yet).

    Returns {"entity_type", "entity_id", "overall_status", "tiers",
    "current_tier"}. Read-only — no RBAC/audit-log write.
    """
    tiers = list_sign_off_approvals_for_entity(conn, entity_type, entity_id)
    if not tiers:
        overall_status = "not_started"
    elif any(t["decision"] == "rejected" for t in tiers):
        overall_status = "rejected"
    elif any(t["decision"] == "returned_for_revision" for t in tiers):
        overall_status = "returned_for_revision"
    elif all(t["decision"] == "approved" for t in tiers):
        overall_status = "approved"
    else:
        overall_status = "in_progress"

    current_tier = next((t for t in tiers if t["decision"] == "pending"), None)
    return {
        "entity_type": entity_type,
        "entity_id": str(entity_id),
        "overall_status": overall_status,
        "tiers": tiers,
        "current_tier": current_tier,
    }


def submit_sign_off_decision(
    conn: psycopg.Connection,
    user_id: UUID | str,
    entity_type: str,
    entity_id: UUID | str,
    sequence_order: int,
    decision: str,
    comments: Optional[str] = None,
) -> dict[str, Any]:
    """Records a decision ('approved' / 'rejected' / 'returned_for_revision')
    for one tier of an entity's sign-off chain.

    Enforcement, in order:
    1. `decision` must be one of _VALID_DECISIONS ('pending' is not a
       submittable decision — it's only the initial state).
    2. The target tier (entity_type, entity_id, sequence_order) must
       exist (NotFoundError otherwise).
    3. **Blocking rule (the core FR14 requirement)**: every tier with a
       lower sequence_order for the same entity must already be
       'approved'. If any earlier tier is still 'pending' (or itself
       'rejected'/'returned_for_revision'), this raises
       SignOffSequenceError naming the blocking tier — progression is
       refused, not silently allowed out of order.
    4. The target tier's current decision must be 'pending' — deciding an
       already-decided tier again raises SignOffAlreadyDecidedError (a
       sign-off is a one-shot signature; see restart_sign_off_chain for
       the explicit reset path).
    5. RBAC: the acting user must hold that tier's `required_role`, or
       'admin' (approver roles are intentionally excluded from
       WRITE_ROLES — see the module docstring).
    """
    if decision not in _VALID_DECISIONS:
        raise BCMPlannerError(
            f"Invalid decision '{decision}'. Must be one of: {', '.join(sorted(_VALID_DECISIONS))}."
        )

    tiers = list_sign_off_approvals_for_entity(conn, entity_type, entity_id)
    target = next((t for t in tiers if t["sequence_order"] == sequence_order), None)
    if target is None:
        raise NotFoundError(
            f"No sign-off tier with sequence_order={sequence_order} exists for {entity_type} {entity_id}."
        )

    blocking = [
        t for t in tiers
        if t["sequence_order"] < sequence_order and t["decision"] != "approved"
    ]
    if blocking:
        earliest = min(blocking, key=lambda t: t["sequence_order"])
        raise SignOffSequenceError(
            f"Cannot decide tier {sequence_order} for {entity_type} {entity_id}: tier "
            f"{earliest['sequence_order']} ({earliest['required_role']}) has not yet been "
            f"approved (current decision: '{earliest['decision']}'). Earlier tiers must all be "
            "'approved' before a later tier can be actioned."
        )

    if target["decision"] != "pending":
        raise SignOffAlreadyDecidedError(
            f"Tier {sequence_order} for {entity_type} {entity_id} was already decided "
            f"'{target['decision']}' (by user {target['approver_user_id']} on "
            f"{target['decision_date']}). Use restart_sign_off_chain(...) to reset the chain "
            "before resubmitting, rather than overwriting a recorded decision."
        )

    require_role(conn, user_id, {target["required_role"], "admin"})

    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE sign_off_approvals
            SET decision = %s, decision_date = CURRENT_TIMESTAMP, comments = %s, approver_user_id = %s
            WHERE approval_id = %s
            RETURNING *
            """,
            (decision, comments, str(user_id), target["approval_id"]),
        )
        row = cur.fetchone()

    log_audit(
        conn, user_id, "APPROVE" if decision == "approved" else "UPDATE", "sign_off_approvals",
        target["approval_id"],
        {"entity_type": entity_type, "entity_id": str(entity_id), "sequence_order": sequence_order, "decision": decision},
    )
    return row


def restart_sign_off_chain(
    conn: psycopg.Connection, user_id: UUID | str, entity_type: str, entity_id: UUID | str
) -> list[dict[str, Any]]:
    """Resets every tier of an entity's sign-off chain back to 'pending'
    (clearing decision/decision_date/comments/approver_user_id) — the
    explicit, auditable way to restart a sign-off cycle after a
    'returned_for_revision' or 'rejected' outcome, once the underlying
    BIA/plan/strategy/report has actually been revised. Deliberately a
    full reset rather than a partial one (re-deciding just one tier would
    let a later 'approved' tier survive a rejection at an earlier tier,
    which is not a sensible state for a sequential sign-off chain).
    """
    require_role(conn, user_id, WRITE_ROLES)
    tiers = list_sign_off_approvals_for_entity(conn, entity_type, entity_id)
    if not tiers:
        raise NotFoundError(f"No sign-off chain exists for {entity_type} {entity_id} to restart.")

    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE sign_off_approvals
            SET decision = 'pending', decision_date = NULL, comments = NULL, approver_user_id = NULL
            WHERE entity_type = %s AND entity_id = %s
            RETURNING *
            """,
            (entity_type, str(entity_id)),
        )
        rows = cur.fetchall()

    log_audit(
        conn, user_id, "UPDATE", "sign_off_approvals", entity_id,
        {"entity_type": entity_type, "action": "restart_sign_off_chain", "tier_count": len(rows)},
    )
    return sorted(rows, key=lambda t: t["sequence_order"])


# ---------------------------------------------------------------------------
# Version control & review-cycle scheduler (FR15)
# ---------------------------------------------------------------------------


def compute_next_review_date(base_date: datetime.date, review_frequency_months: int) -> datetime.date:
    """Adds `review_frequency_months` calendar months to `base_date`.

    Documented heuristic (deliberately simple, no external date library):
    computes the target (year, month) by integer month arithmetic, then
    clamps the day-of-month to the last valid day of the target month if
    the original day doesn't exist there (e.g. 2026-01-31 + 1 month ->
    2026-02-28, not an invalid 2026-02-31 or a silently-wrong rollover to
    2026-03-03). This is the same "simple, sensible default, not a
    sophisticated engine" spirit as Phase 3's escalation heuristic and
    Phase 4's scenario templates.
    """
    if review_frequency_months <= 0:
        raise BCMPlannerError(f"review_frequency_months must be positive, got {review_frequency_months}.")

    total_months = base_date.month - 1 + review_frequency_months
    target_year = base_date.year + total_months // 12
    target_month = total_months % 12 + 1
    last_day_of_target_month = calendar.monthrange(target_year, target_month)[1]
    target_day = min(base_date.day, last_day_of_target_month)
    return datetime.date(target_year, target_month, target_day)


def create_review_schedule(
    conn: psycopg.Connection,
    user_id: UUID | str,
    entity_type: str,
    entity_id: UUID | str,
    review_frequency_months: int = 12,
    last_reviewed_date: Optional[datetime.date] = None,
    next_review_date: Optional[datetime.date] = None,
    review_trigger_type: str = "periodic",
    trigger_event_description: Optional[str] = None,
    notes: Optional[str] = None,
) -> dict[str, Any]:
    """Creates a document_review_schedule row for a governed entity.

    If `next_review_date` is not given:
    - `review_trigger_type='periodic'`: computed via
      compute_next_review_date(last_reviewed_date or today,
      review_frequency_months).
    - `review_trigger_type='event_driven'`: there is no periodic formula
      for an event-driven trigger (it fires on a real-world event, not a
      calendar interval) — `next_review_date` MUST be supplied explicitly,
      or this raises a clear BCMPlannerError rather than silently guessing
      a date.

    One schedule per (entity_type, entity_id) — the DB's
    UNIQUE(entity_type, entity_id) constraint on document_review_schedule
    is the backstop; a second create attempt is translated into a clear
    BCMPlannerError.
    """
    require_role(conn, user_id, WRITE_ROLES)
    _validate_entity_type(entity_type)
    _validate_entity_exists(conn, entity_type, entity_id)

    if next_review_date is None:
        if review_trigger_type == "periodic":
            base = last_reviewed_date or datetime.date.today()
            next_review_date = compute_next_review_date(base, review_frequency_months)
        else:
            raise BCMPlannerError(
                "next_review_date must be supplied explicitly for review_trigger_type="
                "'event_driven' (there is no calendar formula for an event-driven review date)."
            )

    savepoint = "sp_create_review_schedule"
    with conn.cursor() as cur:
        cur.execute(f"SAVEPOINT {savepoint}")
        try:
            cur.execute(
                """
                INSERT INTO document_review_schedule
                    (entity_type, entity_id, review_frequency_months, last_reviewed_date,
                     next_review_date, review_trigger_type, trigger_event_description, notes)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING *
                """,
                (
                    entity_type, str(entity_id), review_frequency_months, last_reviewed_date,
                    next_review_date, review_trigger_type, trigger_event_description, notes,
                ),
            )
            row = cur.fetchone()
        except psycopg.errors.UniqueViolation as exc:
            cur.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
            raise BCMPlannerError(
                f"A review schedule already exists for {entity_type} {entity_id} "
                "(UNIQUE(entity_type, entity_id)). Use update_review_schedule(...) instead."
            ) from exc
        else:
            cur.execute(f"RELEASE SAVEPOINT {savepoint}")

    log_audit(
        conn, user_id, "CREATE", "document_review_schedule", row["schedule_id"],
        {"entity_type": entity_type, "entity_id": str(entity_id), "next_review_date": str(next_review_date)},
    )
    return row


def get_review_schedule(conn: psycopg.Connection, schedule_id: UUID | str) -> dict[str, Any]:
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM document_review_schedule WHERE schedule_id = %s", (str(schedule_id),))
        row = cur.fetchone()
    if row is None:
        raise NotFoundError(f"Review schedule {schedule_id} not found.")
    return row


def get_review_schedule_for_entity(
    conn: psycopg.Connection, entity_type: str, entity_id: UUID | str
) -> Optional[dict[str, Any]]:
    """Reads the review schedule for an entity, if one exists. Returns
    None (not NotFoundError) — "no schedule yet" is a normal state for a
    newly-created entity, same pattern as
    exercise_debrief.get_exercise_debrief_by_exercise.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM document_review_schedule WHERE entity_type = %s AND entity_id = %s",
            (entity_type, str(entity_id)),
        )
        return cur.fetchone()


def update_review_schedule(
    conn: psycopg.Connection,
    user_id: UUID | str,
    schedule_id: UUID | str,
    **fields: Any,
) -> dict[str, Any]:
    """Updates a subset of mutable document_review_schedule fields.

    Allowed fields: review_frequency_months, last_reviewed_date,
    next_review_date, review_trigger_type, trigger_event_description, notes.
    entity_type/entity_id are intentionally NOT updatable here — re-pointing
    a schedule to a different entity would bypass the one-schedule-per-entity
    intent; create a new schedule for a different entity instead.
    """
    require_role(conn, user_id, WRITE_ROLES)
    allowed_fields = {
        "review_frequency_months", "last_reviewed_date", "next_review_date",
        "review_trigger_type", "trigger_event_description", "notes",
    }
    unknown = set(fields) - allowed_fields
    if unknown:
        raise BCMPlannerError(f"Unknown/immutable document_review_schedule field(s): {', '.join(sorted(unknown))}")
    if not fields:
        raise BCMPlannerError("update_review_schedule called with no fields to update.")

    set_clause = ", ".join(f"{k} = %s" for k in fields)
    params = list(fields.values()) + [str(schedule_id)]
    with conn.cursor() as cur:
        cur.execute(f"UPDATE document_review_schedule SET {set_clause} WHERE schedule_id = %s RETURNING *", params)
        row = cur.fetchone()
    if row is None:
        raise NotFoundError(f"Review schedule {schedule_id} not found.")
    log_audit(conn, user_id, "UPDATE", "document_review_schedule", schedule_id, fields)
    return row


def mark_review_completed(
    conn: psycopg.Connection,
    user_id: UUID | str,
    schedule_id: UUID | str,
    reviewed_date: Optional[datetime.date] = None,
    next_review_date: Optional[datetime.date] = None,
) -> dict[str, Any]:
    """Records that a scheduled review happened: sets
    `last_reviewed_date` to `reviewed_date` (default: today) and advances
    `next_review_date`.

    For `review_trigger_type='periodic'` schedules, `next_review_date` is
    auto-computed via compute_next_review_date(reviewed_date,
    review_frequency_months) if not supplied. For `event_driven`
    schedules, `next_review_date` MUST be supplied explicitly (same
    reasoning as create_review_schedule — no calendar formula applies).
    """
    require_role(conn, user_id, WRITE_ROLES)
    schedule = get_review_schedule(conn, schedule_id)
    reviewed_date = reviewed_date or datetime.date.today()

    if next_review_date is None:
        if schedule["review_trigger_type"] == "periodic":
            next_review_date = compute_next_review_date(reviewed_date, schedule["review_frequency_months"])
        else:
            raise BCMPlannerError(
                f"Review schedule {schedule_id} is 'event_driven'; next_review_date must be "
                "supplied explicitly to mark_review_completed (no calendar formula applies)."
            )

    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE document_review_schedule
            SET last_reviewed_date = %s, next_review_date = %s
            WHERE schedule_id = %s
            RETURNING *
            """,
            (reviewed_date, next_review_date, str(schedule_id)),
        )
        row = cur.fetchone()

    log_audit(
        conn, user_id, "UPDATE", "document_review_schedule", schedule_id,
        {"last_reviewed_date": str(reviewed_date), "next_review_date": str(next_review_date)},
    )
    return row


def list_upcoming_review_schedules(
    conn: psycopg.Connection, within_days: int = 90
) -> list[dict[str, Any]]:
    """Returns document_review_schedule rows due within the next
    `within_days` days (inclusive of today, exclusive of overdue rows —
    see get_overdue_review_schedules for those), ordered by
    next_review_date ascending (soonest first). Dashboard/demo feature
    showing the review-cycle scheduler is actually tracked, not just
    configured.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT * FROM document_review_schedule
            WHERE next_review_date >= CURRENT_DATE
              AND next_review_date <= CURRENT_DATE + %s::interval
            ORDER BY next_review_date
            """,
            (f"{within_days} days",),
        )
        return cur.fetchall()


def get_overdue_review_schedules(conn: psycopg.Connection) -> list[dict[str, Any]]:
    """Returns document_review_schedule rows past due
    (next_review_date < today), ordered by next_review_date ascending
    (most overdue first) — same "overdue" framing as Phase 4's
    get_overdue_capa_items."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM document_review_schedule WHERE next_review_date < CURRENT_DATE ORDER BY next_review_date"
        )
        return cur.fetchall()


# ---------------------------------------------------------------------------
# Document version / maintenance log (FR15)
# ---------------------------------------------------------------------------


def create_document_version(
    conn: psycopg.Connection,
    user_id: UUID | str,
    entity_type: str,
    entity_id: UUID | str,
    version_label: str,
    snapshot_json: dict[str, Any],
    change_summary: Optional[str] = None,
) -> dict[str, Any]:
    """Creates a document_versions row — a full JSONB snapshot of an
    entity at a point in time, tagged with a human-assigned
    `version_label` (e.g. '1.0', '1.1', '2.0'). Same table
    `bcp_generator._record_bc_plan_provenance` writes to for Phase 2's
    auto-generation provenance; this is the general-purpose Phase 5
    CRUD-facing entry point spanning every governed entity type, not just
    auto-generated bc_plans.

    Raises DuplicateVersionLabelError if `version_label` already exists
    for this (entity_type, entity_id) — translates
    UNIQUE(entity_type, entity_id, version_label).

    `created_at` is set explicitly via `clock_timestamp()` (real wall-clock
    time at statement execution) rather than relying on the column's
    `DEFAULT CURRENT_TIMESTAMP` (schema/002_...): `CURRENT_TIMESTAMP`
    resolves to the *transaction* start time, so two document_versions rows
    inserted in quick succession within the same transaction/connection
    (e.g. two record_maintenance_update calls back to back, as tests and
    real callers both do) would otherwise get an identical `created_at`,
    making "most recent version" (get_latest_document_version /
    list_document_versions_for_entity's `ORDER BY created_at DESC`)
    non-deterministic between ties. This is an application-layer fix within
    the existing column (same "solve it within existing columns first"
    principle used throughout this codebase) — no schema change needed.
    """
    require_role(conn, user_id, WRITE_ROLES)
    _validate_entity_type(entity_type)
    _validate_entity_exists(conn, entity_type, entity_id)

    savepoint = "sp_create_document_version"
    with conn.cursor() as cur:
        cur.execute(f"SAVEPOINT {savepoint}")
        try:
            cur.execute(
                """
                INSERT INTO document_versions
                    (entity_type, entity_id, version_label, snapshot_json, change_summary, created_by_user_id, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, clock_timestamp())
                RETURNING *
                """,
                (entity_type, str(entity_id), version_label, Jsonb(snapshot_json), change_summary, str(user_id)),
            )
            row = cur.fetchone()
        except psycopg.errors.UniqueViolation as exc:
            cur.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
            raise DuplicateVersionLabelError(
                f"Version '{version_label}' already exists for {entity_type} {entity_id}. "
                "Choose a new version_label, or use record_maintenance_update(...) to auto-increment one."
            ) from exc
        else:
            cur.execute(f"RELEASE SAVEPOINT {savepoint}")

    log_audit(
        conn, user_id, "CREATE", "document_versions", row["version_id"],
        {"entity_type": entity_type, "entity_id": str(entity_id), "version_label": version_label},
    )
    return row


def get_document_version(conn: psycopg.Connection, version_id: UUID | str) -> dict[str, Any]:
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM document_versions WHERE version_id = %s", (str(version_id),))
        row = cur.fetchone()
    if row is None:
        raise NotFoundError(f"Document version {version_id} not found.")
    return row


def list_document_versions_for_entity(
    conn: psycopg.Connection, entity_type: str, entity_id: UUID | str
) -> list[dict[str, Any]]:
    """Returns every document_versions row for an entity, newest first
    (ORDER BY created_at DESC) — the full maintenance/version history for
    a single BIA/BCP/CMP/test report."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM document_versions WHERE entity_type = %s AND entity_id = %s ORDER BY created_at DESC",
            (entity_type, str(entity_id)),
        )
        return cur.fetchall()


def get_latest_document_version(
    conn: psycopg.Connection, entity_type: str, entity_id: UUID | str
) -> Optional[dict[str, Any]]:
    """Returns the most recent document_versions row for an entity, or
    None if it has never been versioned."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT * FROM document_versions
            WHERE entity_type = %s AND entity_id = %s
            ORDER BY created_at DESC LIMIT 1
            """,
            (entity_type, str(entity_id)),
        )
        return cur.fetchone()


def _next_minor_version_label(current_label: Optional[str]) -> str:
    """Computes the next 'major.minor' version_label given the current
    latest one (or None for a first version). Documented heuristic:
    parses `current_label` as "<int>.<int>" and increments the minor
    component by one (e.g. '1.0' -> '1.1', '1.9' -> '1.10'); starts at
    '1.0' if there is no prior version, or if the current label doesn't
    parse as "<int>.<int>" (a hand-authored non-numeric label, e.g.
    'draft') — in which case this falls back to '1.0' rather than
    guessing at a numeric successor to free-text. Deliberately simple —
    not semantic-versioning-aware (no major-version-bump rule), matching
    the "lightweight state machine, no enterprise workflow engine" scope
    of this phase.
    """
    if current_label is None:
        return "1.0"
    parts = current_label.split(".")
    if len(parts) == 2 and all(p.isdigit() for p in parts):
        major, minor = int(parts[0]), int(parts[1])
        return f"{major}.{minor + 1}"
    return "1.0"


def record_maintenance_update(
    conn: psycopg.Connection,
    user_id: UUID | str,
    entity_type: str,
    entity_id: UUID | str,
    change_summary: str,
    snapshot_json: Optional[dict[str, Any]] = None,
    version_label: Optional[str] = None,
) -> dict[str, Any]:
    """Convenience wrapper around create_document_version for the
    "maintenance update" use case (FR15: "log all maintenance updates
    across BIA parameters, BCPs, CMPs, and test reports"): auto-computes
    the next version_label via _next_minor_version_label if one isn't
    given, and defaults snapshot_json to {} (document_versions.snapshot_json
    is NOT NULL) if the caller only wants to log a change_summary without a
    full content snapshot — the version/maintenance log entry itself
    (entity_type + entity_id + version_label + change_summary + timestamp
    + created_by_user_id) is the point; a full snapshot is optional detail.
    """
    latest = get_latest_document_version(conn, entity_type, entity_id)
    resolved_label = version_label or _next_minor_version_label(latest["version_label"] if latest else None)
    return create_document_version(
        conn, user_id, entity_type, entity_id, resolved_label,
        snapshot_json if snapshot_json is not None else {}, change_summary,
    )
