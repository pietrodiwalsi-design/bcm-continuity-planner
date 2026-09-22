"""Modular Exercise & Test Planner — Phase 4 of the BCM Continuity Planner.

Implements, against the PostgreSQL schema in schema/001_core_schema.sql
(the `exercise_programmes` and `exercises` tables were already defined
there — no new migration was needed for this phase), covering FR11:

- CRUD for `exercise_programmes`: annual exercise programmes (title,
  annual_schedule_year, objectives, approved_budget), scoped to
  `organization_id`.
- CRUD for `exercises`: individual exercises/tests (category, title,
  planned_date, lead_facilitator, scenario_description, status), linked
  to a `programme_id`, using the existing `exercise_category_enum`
  (discussion_based / scenario_tabletop / simulation / live /
  functional_test).
- `get_disruption_scenario_template(scenario_type)`: a rule-based helper
  providing pre-configured disruption scenario templates for common
  scenario types (power outage, cyberattack/DDoS, data breach, transit
  disruption, key supplier failure), each returning a suggested
  scenario_description, suggested exercise category, and suggested
  objectives. Scenario type naming aligned with
  `crisis_communications.HOLDING_STATEMENT_TEMPLATES` where sensible
  (`power_outage`, `cyberattack_ddos`, `data_breach`,
  `public_transit_disruption`) plus one Phase-4-specific addition
  (`key_supplier_failure`). Full template set documented in
  `docs/exercise_scenario_templates.md` — this module is the single
  source of truth for the actual template content; that file is a
  human-readable summary of it (same pattern as
  `docs/bcp_generation_rules.md` / `docs/crisis_communication_templates.md`
  for Phases 2/3).

Conventions reused, unchanged, from bia_engine.py (Phase 1) /
bcp_generator.py (Phase 2) / crisis_management.py + crisis_communications.py
(Phase 3) per the Phase 4 brief — this module does not reinvent RBAC,
audit logging, error handling, or connection style:

- psycopg (v3), dict-row cursors via bcm_planner.db.get_connection().
- `bia_engine.require_role` for RBAC (same WRITE_ROLES allow-list).
- `bia_engine.log_audit` for append-only audit logging.
- `bia_engine.BCMPlannerError` / `NotFoundError` / `InsufficientRoleError`
  as the shared exception hierarchy.
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
    "create_exercise_programme",
    "get_exercise_programme",
    "update_exercise_programme",
    "list_exercise_programmes_by_organization",
    "create_exercise",
    "get_exercise",
    "update_exercise",
    "list_exercises_by_programme",
    "normalize_scenario_type",
    "get_disruption_scenario_template",
    "DISRUPTION_SCENARIO_TEMPLATES",
]


# ---------------------------------------------------------------------------
# exercise_programmes CRUD
# ---------------------------------------------------------------------------


def create_exercise_programme(
    conn: psycopg.Connection,
    user_id: UUID | str,
    organization_id: UUID | str,
    title: str,
    annual_schedule_year: int,
    objectives: str,
    approved_budget: Optional[float] = None,
) -> dict[str, Any]:
    """Creates an exercise_programmes row (an annual exercise/test
    programme), scoped to an organization.
    """
    require_role(conn, user_id, WRITE_ROLES)
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO exercise_programmes
                (organization_id, title, annual_schedule_year, objectives, approved_budget)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING *
            """,
            (str(organization_id), title, annual_schedule_year, objectives, approved_budget),
        )
        row = cur.fetchone()
    log_audit(
        conn, user_id, "CREATE", "exercise_programmes", row["programme_id"],
        {"title": title, "annual_schedule_year": annual_schedule_year},
        organization_id=organization_id,
    )
    return row


def get_exercise_programme(conn: psycopg.Connection, programme_id: UUID | str) -> dict[str, Any]:
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM exercise_programmes WHERE programme_id = %s", (str(programme_id),))
        row = cur.fetchone()
    if row is None:
        raise NotFoundError(f"Exercise programme {programme_id} not found.")
    return row


def list_exercise_programmes_by_organization(
    conn: psycopg.Connection, organization_id: UUID | str
) -> list[dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM exercise_programmes WHERE organization_id = %s ORDER BY annual_schedule_year DESC, title",
            (str(organization_id),),
        )
        return cur.fetchall()


def update_exercise_programme(
    conn: psycopg.Connection,
    user_id: UUID | str,
    programme_id: UUID | str,
    **fields: Any,
) -> dict[str, Any]:
    """Updates a subset of mutable exercise_programmes fields.

    Allowed fields: title, annual_schedule_year, objectives, approved_budget.
    """
    require_role(conn, user_id, WRITE_ROLES)
    allowed_fields = {"title", "annual_schedule_year", "objectives", "approved_budget"}
    unknown = set(fields) - allowed_fields
    if unknown:
        raise BCMPlannerError(f"Unknown/immutable exercise_programmes field(s): {', '.join(sorted(unknown))}")
    if not fields:
        raise BCMPlannerError("update_exercise_programme called with no fields to update.")

    set_clause = ", ".join(f"{k} = %s" for k in fields)
    params = list(fields.values()) + [str(programme_id)]
    with conn.cursor() as cur:
        cur.execute(f"UPDATE exercise_programmes SET {set_clause} WHERE programme_id = %s RETURNING *", params)
        row = cur.fetchone()
    if row is None:
        raise NotFoundError(f"Exercise programme {programme_id} not found.")
    log_audit(conn, user_id, "UPDATE", "exercise_programmes", programme_id, fields)
    return row


# ---------------------------------------------------------------------------
# exercises CRUD
# ---------------------------------------------------------------------------


def create_exercise(
    conn: psycopg.Connection,
    user_id: UUID | str,
    programme_id: UUID | str,
    category: str,
    title: str,
    planned_date: datetime.date,
    lead_facilitator: str,
    scenario_description: str,
    status: str = "Scheduled",
) -> dict[str, Any]:
    """Creates an exercises row under an exercise_programmes row. `category`
    must be one of exercise_category_enum's values (discussion_based/
    scenario_tabletop/simulation/live/functional_test) — the DB rejects
    invalid values with a clear error. Validates programme_id exists first
    so callers get a NotFoundError rather than a raw FK violation.
    """
    require_role(conn, user_id, WRITE_ROLES)
    get_exercise_programme(conn, programme_id)  # raises NotFoundError if missing
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO exercises
                (programme_id, category, title, planned_date, lead_facilitator, scenario_description, status)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            RETURNING *
            """,
            (str(programme_id), category, title, planned_date, lead_facilitator, scenario_description, status),
        )
        row = cur.fetchone()
    log_audit(
        conn, user_id, "CREATE", "exercises", row["exercise_id"],
        {"programme_id": str(programme_id), "category": category, "title": title, "planned_date": str(planned_date)},
    )
    return row


def get_exercise(conn: psycopg.Connection, exercise_id: UUID | str) -> dict[str, Any]:
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM exercises WHERE exercise_id = %s", (str(exercise_id),))
        row = cur.fetchone()
    if row is None:
        raise NotFoundError(f"Exercise {exercise_id} not found.")
    return row


def list_exercises_by_programme(conn: psycopg.Connection, programme_id: UUID | str) -> list[dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM exercises WHERE programme_id = %s ORDER BY planned_date",
            (str(programme_id),),
        )
        return cur.fetchall()


def update_exercise(
    conn: psycopg.Connection,
    user_id: UUID | str,
    exercise_id: UUID | str,
    **fields: Any,
) -> dict[str, Any]:
    """Updates a subset of mutable exercises fields.

    Allowed fields: category, title, planned_date, lead_facilitator,
    scenario_description, status.
    """
    require_role(conn, user_id, WRITE_ROLES)
    allowed_fields = {
        "category", "title", "planned_date", "lead_facilitator",
        "scenario_description", "status",
    }
    unknown = set(fields) - allowed_fields
    if unknown:
        raise BCMPlannerError(f"Unknown/immutable exercises field(s): {', '.join(sorted(unknown))}")
    if not fields:
        raise BCMPlannerError("update_exercise called with no fields to update.")

    set_clause = ", ".join(f"{k} = %s" for k in fields)
    params = list(fields.values()) + [str(exercise_id)]
    with conn.cursor() as cur:
        cur.execute(f"UPDATE exercises SET {set_clause} WHERE exercise_id = %s RETURNING *", params)
        row = cur.fetchone()
    if row is None:
        raise NotFoundError(f"Exercise {exercise_id} not found.")
    log_audit(conn, user_id, "UPDATE", "exercises", exercise_id, fields)
    return row


# ---------------------------------------------------------------------------
# Rule-based disruption scenario templates (FR11)
# See docs/exercise_scenario_templates.md for the human-readable summary.
# ---------------------------------------------------------------------------


def normalize_scenario_type(scenario_type: str) -> str:
    """Normalizes a scenario_type string (case/space/hyphen/slash
    insensitive) to the canonical underscore-separated key used by
    DISRUPTION_SCENARIO_TEMPLATES and (in scenario_injects.py)
    INJECT_TEMPLATES_BY_SCENARIO. Same normalization rule as
    crisis_communications.generate_holding_statement_draft, duplicated
    here (not imported) to keep this module's public template dict
    self-contained and independently testable.
    """
    return scenario_type.strip().lower().replace(" ", "_").replace("-", "_").replace("/", "_")


# scenario_type (normalized) -> {suggested_scenario_description,
# suggested_category, suggested_objectives}. `suggested_category` values
# match exercise_category_enum exactly. Scenario type keys for
# power_outage / cyberattack_ddos / data_breach / public_transit_disruption
# intentionally match crisis_communications.HOLDING_STATEMENT_TEMPLATES so
# the same scenario_type string can drive both a crisis communication
# draft (Phase 3) and an exercise scenario template (Phase 4) for the same
# underlying disruption. key_supplier_failure is a Phase-4-only addition
# (FR11 explicitly calls out "key supplier failure" as a disruption type;
# Phase 3's message_bank scenario_type set did not need a supplier-failure
# holding statement, so no equivalent exists there).
DISRUPTION_SCENARIO_TEMPLATES: dict[str, dict[str, Any]] = {
    "power_outage": {
        "suggested_scenario_description": (
            "An extended, unplanned power outage affects the primary site, taking down "
            "on-site systems, facilities equipment, and physical access controls. Backup "
            "generator/UPS capacity is assumed limited or partially failed to force a "
            "genuine test of alternate-site/remote-working arrangements."
        ),
        "suggested_category": "scenario_tabletop",
        "suggested_objectives": [
            "Validate invocation criteria and decision authority for declaring a site-level disruption.",
            "Test activation of backup power / alternate site procedures and their actual recovery time against the documented RTO.",
            "Confirm crisis communication holding statements are issued to affected stakeholders within the escalation trigger's notification timeframe.",
        ],
    },
    "cyberattack_ddos": {
        "suggested_scenario_description": (
            "A cyberattack (e.g. ransomware encryption spreading across file shares, or a "
            "sustained DDoS against customer-facing services) degrades or takes down IT "
            "systems supporting one or more prioritised activities, with escalating impact "
            "over the exercise timeline."
        ),
        "suggested_category": "simulation",
        "suggested_objectives": [
            "Test the IT/Security incident response handoff into the wider Crisis Management Team escalation path.",
            "Validate manual workaround / degraded-mode procedures for activities dependent on the affected systems.",
            "Exercise the cyberattack_ddos holding statement draft-and-legal-review workflow before any external communication.",
        ],
    },
    "data_breach": {
        "suggested_scenario_description": (
            "Unauthorized access to and exfiltration of customer or personnel data is "
            "discovered, triggering data protection/regulatory notification considerations "
            "alongside the operational and reputational response."
        ),
        "suggested_category": "scenario_tabletop",
        "suggested_objectives": [
            "Test the interaction between IT/Security, Legal Counsel, and the Crisis Management Team Leader on regulatory notification timing.",
            "Validate that the data_breach holding statement's legal-review flag is respected before any stakeholder communication is issued.",
            "Identify gaps in the stakeholder contact matrix for regulator and affected-customer notification.",
        ],
    },
    "public_transit_disruption": {
        "suggested_scenario_description": (
            "A significant, multi-hour public transit disruption prevents a large "
            "proportion of on-site personnel from reaching the primary site, without any "
            "physical damage to the site itself."
        ),
        "suggested_category": "discussion_based",
        "suggested_objectives": [
            "Confirm remote-working / work-from-home continuity procedures can be invoked quickly for affected personnel.",
            "Test communication of alternate arrangements to staff and, where relevant, customers/stakeholders.",
            "Assess whether MBCO (minimum acceptable delivery capacity) can still be met with a reduced on-site workforce.",
        ],
    },
    "key_supplier_failure": {
        "suggested_scenario_description": (
            "A single-point-of-failure key supplier (identified via the BIA's resource "
            "dependency mapping / SPOF detection) fails to deliver, threatening one or more "
            "prioritised activities' RTO if not remediated."
        ),
        "suggested_category": "functional_test",
        "suggested_objectives": [
            "Validate the recovery strategy's assumptions about alternate/backup supplier arrangements.",
            "Test the resource dependency tracking data (activity_resource_dependencies) against what actually happens when the primary supplier is unavailable.",
            "Confirm vendor management and process owner roles are clear on who initiates alternate supplier engagement.",
        ],
    },
}


def get_disruption_scenario_template(scenario_type: str) -> dict[str, Any]:
    """Returns a pre-configured disruption scenario template for a
    recognized `scenario_type` (power_outage, cyberattack_ddos,
    data_breach, public_transit_disruption, key_supplier_failure —
    matched case/space/hyphen/slash-insensitively via
    normalize_scenario_type). Each template contains
    `suggested_scenario_description`, `suggested_category` (an
    exercise_category_enum value), and `suggested_objectives` (a list of
    strings).

    Unlike crisis_communications.generate_holding_statement_draft (which
    falls back to a generic template for unrecognized scenario_type
    values, since a holding statement must always be producible even for
    an ad-hoc incident), this helper deliberately raises a clear
    BCMPlannerError for an unrecognized scenario_type rather than
    fabricating a plausible-sounding but ungrounded exercise scenario —
    an exercise facilitator should consciously author a bespoke scenario
    rather than receive templated content that does not actually match
    their intended disruption type. See
    docs/exercise_scenario_templates.md section 4 for this documented
    fallback/error behavior.

    Pure helper — does not touch the database.
    """
    normalized = normalize_scenario_type(scenario_type)
    if normalized not in DISRUPTION_SCENARIO_TEMPLATES:
        known = ", ".join(sorted(DISRUPTION_SCENARIO_TEMPLATES))
        raise BCMPlannerError(
            f"No pre-configured disruption scenario template exists for scenario_type "
            f"'{scenario_type}' (normalized: '{normalized}'). Known scenario types: {known}. "
            "See docs/exercise_scenario_templates.md, or author a bespoke exercise via "
            "create_exercise() directly instead of relying on a template for this scenario type."
        )
    template = DISRUPTION_SCENARIO_TEMPLATES[normalized]
    return {"scenario_type": normalized, **template}
