"""Scenario Injects & Timeline Storyboarding — Phase 4 of the BCM
Continuity Planner.

Implements, against the PostgreSQL schema in schema/001_core_schema.sql
(the `scenario_injects` table was already defined there — no new
migration was needed for this phase), covering FR12:

- CRUD for `scenario_injects`: time-phased inject events (sequence_number,
  time_offset_minutes, inject_title, inject_content, delivery_method,
  expected_team_action), linked to an `exercise_id`.
- `get_exercise_storyboard(exercise_id)`: returns all injects for an
  exercise ordered by `time_offset_minutes` (the storyboard timeline),
  with a validation check that `sequence_number` values are unique and
  strictly increasing in step with `time_offset_minutes` order — a
  facilitator authoring error otherwise (e.g. two injects sharing a
  sequence_number, or an earlier-timed inject numbered after a
  later-timed one). Raises `StoryboardValidationError` (a
  `BCMPlannerError` subclass) with a clear message instead of silently
  returning a broken storyboard.
- `generate_injects_from_scenario_template(exercise_id, scenario_type)`: a
  rule-based helper that, given the same `scenario_type` values used by
  `exercise_planner.get_disruption_scenario_template`, generates a
  starter set of 3-5 time-phased injects appropriate to that scenario
  (e.g. for cyberattack_ddos: T+0 "initial detection alert", T+15 "IT
  confirms ransomware encryption spreading", ...). Rule-based/templated,
  not AI-generated prose, consistent with the Phase 2/3 approach. Full
  inject sets documented in `docs/exercise_scenario_templates.md` — this
  module is the single source of truth for the actual template content;
  that file is a human-readable summary of it.

Conventions reused, unchanged, from bia_engine.py (Phase 1) /
bcp_generator.py (Phase 2) / crisis_management.py + crisis_communications.py
(Phase 3) / exercise_planner.py (this phase) per the Phase 4 brief — this
module does not reinvent RBAC, audit logging, error handling, or
connection style:

- psycopg (v3), dict-row cursors via bcm_planner.db.get_connection().
- `bia_engine.require_role` for RBAC (same WRITE_ROLES allow-list).
- `bia_engine.log_audit` for append-only audit logging.
- `bia_engine.BCMPlannerError` / `NotFoundError` / `InsufficientRoleError`
  as the shared exception hierarchy (this module adds one new leaf
  exception, `StoryboardValidationError`).
"""

from __future__ import annotations

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
from bcm_planner.exercise_planner import normalize_scenario_type

__all__ = [
    "BCMPlannerError",
    "InsufficientRoleError",
    "NotFoundError",
    "StoryboardValidationError",
    "create_scenario_inject",
    "get_scenario_inject",
    "update_scenario_inject",
    "list_scenario_injects_by_exercise",
    "get_exercise_storyboard",
    "generate_injects_from_scenario_template",
    "INJECT_TEMPLATES_BY_SCENARIO",
]


class StoryboardValidationError(BCMPlannerError):
    """Raised when a storyboard's scenario_injects rows have duplicate
    sequence_number values, or sequence_number does not strictly increase
    in step with time_offset_minutes order — a facilitator authoring
    error that would otherwise silently produce a broken/ambiguous
    exercise timeline.
    """


# ---------------------------------------------------------------------------
# scenario_injects CRUD
# ---------------------------------------------------------------------------


def create_scenario_inject(
    conn: psycopg.Connection,
    user_id: UUID | str,
    exercise_id: UUID | str,
    sequence_number: int,
    time_offset_minutes: int,
    inject_title: str,
    inject_content: str,
    delivery_method: str,
    expected_team_action: str,
) -> dict[str, Any]:
    """Creates a scenario_injects row under an exercise. No uniqueness/
    ordering validation happens here (a single insert does not know about
    sibling rows) — call get_exercise_storyboard(exercise_id) after
    authoring a full inject set to validate the storyboard as a whole.
    """
    require_role(conn, user_id, WRITE_ROLES)
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO scenario_injects
                (exercise_id, sequence_number, time_offset_minutes, inject_title,
                 inject_content, delivery_method, expected_team_action)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            RETURNING *
            """,
            (
                str(exercise_id), sequence_number, time_offset_minutes, inject_title,
                inject_content, delivery_method, expected_team_action,
            ),
        )
        row = cur.fetchone()
    log_audit(
        conn, user_id, "CREATE", "scenario_injects", row["inject_id"],
        {"exercise_id": str(exercise_id), "sequence_number": sequence_number, "time_offset_minutes": time_offset_minutes},
    )
    return row


def get_scenario_inject(conn: psycopg.Connection, inject_id: UUID | str) -> dict[str, Any]:
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM scenario_injects WHERE inject_id = %s", (str(inject_id),))
        row = cur.fetchone()
    if row is None:
        raise NotFoundError(f"Scenario inject {inject_id} not found.")
    return row


def list_scenario_injects_by_exercise(conn: psycopg.Connection, exercise_id: UUID | str) -> list[dict[str, Any]]:
    """Lists all scenario_injects for an exercise, ordered by
    time_offset_minutes (the storyboard timeline order), with
    sequence_number as a tiebreaker. Does not validate ordering — see
    get_exercise_storyboard for the validated read.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM scenario_injects WHERE exercise_id = %s ORDER BY time_offset_minutes, sequence_number",
            (str(exercise_id),),
        )
        return cur.fetchall()


def update_scenario_inject(
    conn: psycopg.Connection,
    user_id: UUID | str,
    inject_id: UUID | str,
    **fields: Any,
) -> dict[str, Any]:
    """Updates a subset of mutable scenario_injects fields.

    Allowed fields: sequence_number, time_offset_minutes, inject_title,
    inject_content, delivery_method, expected_team_action.
    """
    require_role(conn, user_id, WRITE_ROLES)
    allowed_fields = {
        "sequence_number", "time_offset_minutes", "inject_title",
        "inject_content", "delivery_method", "expected_team_action",
    }
    unknown = set(fields) - allowed_fields
    if unknown:
        raise BCMPlannerError(f"Unknown/immutable scenario_injects field(s): {', '.join(sorted(unknown))}")
    if not fields:
        raise BCMPlannerError("update_scenario_inject called with no fields to update.")

    set_clause = ", ".join(f"{k} = %s" for k in fields)
    params = list(fields.values()) + [str(inject_id)]
    with conn.cursor() as cur:
        cur.execute(f"UPDATE scenario_injects SET {set_clause} WHERE inject_id = %s RETURNING *", params)
        row = cur.fetchone()
    if row is None:
        raise NotFoundError(f"Scenario inject {inject_id} not found.")
    log_audit(conn, user_id, "UPDATE", "scenario_injects", inject_id, fields)
    return row


# ---------------------------------------------------------------------------
# Storyboard validation & read (FR12)
# ---------------------------------------------------------------------------


def _validate_storyboard_order(injects: list[dict[str, Any]]) -> None:
    """Walks a list of scenario_injects rows already ordered by
    time_offset_minutes (ascending) and raises StoryboardValidationError
    if:
    - two injects share the same sequence_number (ambiguous authoring
      order), or
    - sequence_number does not strictly increase as time_offset_minutes
      increases (an inject scheduled later in the timeline was numbered
      before one scheduled earlier — a facilitator authoring error).

    A single pass suffices since `injects` is already time-ordered: each
    inject's sequence_number must be strictly greater than the previous
    one's.
    """
    seen_sequence_numbers: set[int] = set()
    previous_sequence_number: Optional[int] = None
    previous_time_offset: Optional[int] = None

    for inject in injects:
        seq = inject["sequence_number"]
        offset = inject["time_offset_minutes"]

        if seq in seen_sequence_numbers:
            raise StoryboardValidationError(
                f"Storyboard authoring error for exercise {inject['exercise_id']}: "
                f"sequence_number {seq} is used by more than one scenario_injects row. "
                "sequence_number values must be unique within an exercise's storyboard."
            )
        seen_sequence_numbers.add(seq)

        if previous_sequence_number is not None and seq <= previous_sequence_number:
            raise StoryboardValidationError(
                f"Storyboard out of order for exercise {inject['exercise_id']}: the inject at "
                f"time_offset_minutes={offset} has sequence_number={seq}, which is not greater "
                f"than the previous inject's sequence_number={previous_sequence_number} (at "
                f"time_offset_minutes={previous_time_offset}). sequence_number must increase "
                "strictly as time_offset_minutes increases — review and renumber the storyboard "
                "before running this exercise."
            )

        previous_sequence_number = seq
        previous_time_offset = offset


def get_exercise_storyboard(conn: psycopg.Connection, exercise_id: UUID | str) -> dict[str, Any]:
    """Returns the validated storyboard (time-ordered list of
    scenario_injects) for an exercise: {"exercise_id": ..., "injects": [...]}.

    Raises StoryboardValidationError if the injects for this exercise have
    duplicate sequence_number values, or sequence_number does not
    strictly increase in step with time_offset_minutes order — see
    _validate_storyboard_order. Returns an empty inject list (no error)
    for an exercise with no injects yet authored.
    """
    injects = list_scenario_injects_by_exercise(conn, exercise_id)
    _validate_storyboard_order(injects)
    return {"exercise_id": str(exercise_id), "injects": injects}


# ---------------------------------------------------------------------------
# Rule-based inject generation from a disruption scenario template (FR12)
# See docs/exercise_scenario_templates.md for the human-readable summary.
# ---------------------------------------------------------------------------

# scenario_type (normalized, same keys as
# exercise_planner.DISRUPTION_SCENARIO_TEMPLATES) -> starter inject set.
# Each tuple: (sequence_number, time_offset_minutes, inject_title,
# inject_content, delivery_method, expected_team_action).
# 3-5 time-phased injects per scenario, per the Phase 4 brief.
INJECT_TEMPLATES_BY_SCENARIO: dict[str, list[tuple[int, int, str, str, str, str]]] = {
    "power_outage": [
        (
            1, 0, "Initial power loss reported",
            "Facilities reports a total, unplanned loss of mains power at the primary site. "
            "Building access control and lighting are on generator/UPS backup only.",
            "Facilitator Announcement",
            "Confirm plan invocation criteria are met; check backup generator/UPS status and estimated runtime.",
        ),
        (
            2, 15, "Generator failure confirmed",
            "The backup generator has failed to sustain full building load; only critical "
            "life-safety and minimal IT systems remain powered.",
            "Simulated Phone Call",
            "Escalate to the Facilities Lead; determine estimated outage duration; evaluate alternate/standby site activation.",
        ),
        (
            3, 45, "Media inquiry received about outage impact",
            "A local news outlet contacts the organization asking about the outage's impact "
            "on customer-facing services.",
            "Simulated Email",
            "Route the inquiry to the Spokesperson; issue a power_outage holding statement per the crisis communications plan before responding.",
        ),
        (
            4, 90, "Power restored, systems require restart",
            "Mains power is restored, but IT systems and equipment need a controlled restart "
            "and validation before operations can resume.",
            "Facilitator Announcement",
            "Begin post-outage systems validation (Return-to-BAU style checks) before declaring normal operations resumed.",
        ),
    ],
    "cyberattack_ddos": [
        (
            1, 0, "Initial detection alert",
            "The security monitoring platform raises an alert for anomalous encryption "
            "activity across multiple file shares.",
            "Simulated Email",
            "IT/Security Head triages the alert; determine scope and whether this meets escalation trigger criteria.",
        ),
        (
            2, 15, "IT confirms ransomware encryption spreading",
            "IT confirms the activity is ransomware and that encryption is actively spreading "
            "to additional systems.",
            "Facilitator Announcement",
            "Isolate affected systems/network segments; invoke the Crisis Management Team escalation path for this severity.",
        ),
        (
            3, 30, "Law enforcement notification consideration raised",
            "Legal Counsel raises whether law enforcement and/or regulators need to be "
            "notified given the scope of the incident.",
            "Simulated Phone Call",
            "Legal Counsel and CMT Leader jointly assess notification obligations and timing.",
        ),
        (
            4, 45, "Media inquiry received",
            "A journalist contacts the organization asking whether a cyberattack is causing "
            "a reported service outage.",
            "Simulated Email",
            "Route to Spokesperson; issue the cyberattack_ddos holding statement draft only after legal review, per the Phase 3 safety rule.",
        ),
        (
            5, 90, "Ransom note discovered",
            "IT locates a ransom note on affected systems demanding payment in exchange for a decryption key.",
            "Facilitator Announcement",
            "Do not engage with the demand directly; escalate to Legal Counsel and CMT Leader for a coordinated decision per organizational policy.",
        ),
    ],
    "data_breach": [
        (
            1, 0, "Anomalous data access alert triggered",
            "Monitoring detects an unusually large data export from a system holding customer records.",
            "Simulated Email",
            "IT/Security Head investigates scope and confirms whether this is a genuine unauthorized access event.",
        ),
        (
            2, 20, "Unauthorized export of customer records confirmed",
            "Investigation confirms customer records were exported by an unauthorized actor.",
            "Facilitator Announcement",
            "Escalate to Legal Counsel and the CMT Leader; begin scoping which individuals/records are affected.",
        ),
        (
            3, 40, "Regulatory notification obligation flagged",
            "Legal Counsel flags a statutory notification clock (e.g. a 72-hour regulatory "
            "reporting window) that may already be running.",
            "Simulated Phone Call",
            "Confirm the notification clock start time and the regulatory bodies/stakeholders that must be informed and by when.",
        ),
        (
            4, 60, "Customer complaint received via social media",
            "A customer posts publicly referencing leaked personal data, before any official communication has gone out.",
            "Simulated Social Media Post",
            "Route to Spokesperson; only use the data_breach holding statement after explicit legal review — do not respond ad hoc.",
        ),
    ],
    "public_transit_disruption": [
        (
            1, 0, "Transit provider reports unplanned service suspension",
            "The regional transit provider announces a total suspension of a key commuter "
            "route serving the primary site, with no confirmed restoration time.",
            "Facilitator Announcement",
            "Assess the proportion of on-site staff likely affected; consider invoking remote-working continuity procedures.",
        ),
        (
            2, 15, "Staff report unable to reach office",
            "A significant proportion of on-site personnel report they cannot reach the "
            "office due to the transit disruption.",
            "Simulated Email",
            "Confirm headcount impact against MBCO; invoke work-from-home / remote arrangements for affected roles.",
        ),
        (
            3, 30, "Remote-working procedures invoked",
            "Remote-working arrangements are activated for affected staff; some systems "
            "require VPN/remote access verification.",
            "Facilitator Announcement",
            "IT confirms remote access/tooling availability; Process Owner redirects customer-facing interactions to remote-capable channels.",
        ),
        (
            4, 60, "Restoration estimate revised to end-of-day",
            "The transit provider revises its restoration estimate, now indicating service will not resume until end-of-day.",
            "Simulated Phone Call",
            "Reassess whether the extended timeline requires further communication to staff/customers about ongoing reduced on-site capacity.",
        ),
    ],
    "key_supplier_failure": [
        (
            1, 0, "Key supplier notifies inability to deliver",
            "A key supplier, flagged in the BIA's resource dependency mapping as a single "
            "point of failure, notifies that it cannot fulfil contracted deliveries.",
            "Simulated Email",
            "Vendor Management Lead confirms scope and duration of the supplier's inability to deliver.",
        ),
        (
            2, 30, "Inventory review confirms limited runway",
            "An inventory/stock review confirms current supply will be exhausted well before "
            "an alternate arrangement can realistically be in place.",
            "Facilitator Announcement",
            "Escalate to the Process Owner and Recovery Team Lead; check the recovery strategy's alternate-supplier assumptions.",
        ),
        (
            3, 60, "Alternate supplier engagement initiated",
            "The organization begins contacting alternate/backup suppliers identified in the "
            "recovery strategy.",
            "Simulated Phone Call",
            "Vendor Management Lead documents alternate supplier lead times and terms; assess against the activity's RTO.",
        ),
        (
            4, 120, "Delivery delays become public",
            "Customer-facing delivery delays resulting from the supplier failure begin to "
            "surface publicly (e.g. customer complaints, social media).",
            "Simulated Social Media Post",
            "Route to Spokesperson for a coordinated response; confirm messaging is consistent with the actual remediation timeline.",
        ),
    ],
}


def generate_injects_from_scenario_template(
    conn: psycopg.Connection,
    user_id: UUID | str,
    exercise_id: UUID | str,
    scenario_type: str,
) -> list[dict[str, Any]]:
    """Auto-generates a starter set of 3-5 time-phased scenario_injects
    rows for an existing exercise, appropriate to `scenario_type` (the
    same scenario_type values used by
    exercise_planner.get_disruption_scenario_template — matched
    case/space/hyphen/slash-insensitively).

    Rule-based/templated, not AI-generated prose — see
    docs/exercise_scenario_templates.md for the full inject sets this
    function implements. Raises BCMPlannerError for an unrecognized
    scenario_type (same fallback/error philosophy as
    get_disruption_scenario_template — no fabricated generic inject set).

    Validates exercise_id exists first (raises NotFoundError if not).
    Returns the list of created scenario_injects rows, in generation
    order (which is already time-ordered by construction).
    """
    require_role(conn, user_id, WRITE_ROLES)

    # Validate the exercise exists before generating anything against it.
    with conn.cursor() as cur:
        cur.execute("SELECT exercise_id FROM exercises WHERE exercise_id = %s", (str(exercise_id),))
        if cur.fetchone() is None:
            raise NotFoundError(f"Exercise {exercise_id} not found.")

    normalized = normalize_scenario_type(scenario_type)
    if normalized not in INJECT_TEMPLATES_BY_SCENARIO:
        known = ", ".join(sorted(INJECT_TEMPLATES_BY_SCENARIO))
        raise BCMPlannerError(
            f"No pre-configured inject template set exists for scenario_type "
            f"'{scenario_type}' (normalized: '{normalized}'). Known scenario types: {known}. "
            "See docs/exercise_scenario_templates.md, or author bespoke injects via "
            "create_scenario_inject() directly instead."
        )

    templates = INJECT_TEMPLATES_BY_SCENARIO[normalized]
    created: list[dict[str, Any]] = []
    for seq, offset, title, content, delivery_method, expected_action in templates:
        row = create_scenario_inject(
            conn, user_id, exercise_id, seq, offset, title, content, delivery_method, expected_action
        )
        created.append(row)

    log_audit(
        conn, user_id, "CREATE", "scenario_injects", exercise_id,
        {"auto_generated": True, "scenario_type": normalized, "inject_count": len(created)},
    )
    return created
