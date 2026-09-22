"""Crisis Communication & Message Bank Builder — Phase 3 of the BCM
Continuity Planner.

Implements, against the PostgreSQL schema in schema/001_core_schema.sql
(the `stakeholder_contact_matrices` and `message_bank` tables were already
defined there — no new migration was needed for this phase), covering
FR10:

- CRUD for `stakeholder_contact_matrices`: who to notify (stakeholder
  group, contact person/entity, primary/backup channel, notification
  priority), scoped to `organization_id`.
- CRUD for `message_bank`: pre-approved holding statement templates per
  scenario type + target audience, with a `dispatch_channels` array and a
  `pre_approved_by_legal` safety flag.
- `generate_holding_statement_draft`: a rule-based auto-generate helper
  that produces a DRAFT holding statement template for common
  `scenario_type` values, with fill-in-the-blank placeholder tokens. The
  full template set is documented in
  `docs/crisis_communication_templates.md` — this module is the single
  source of truth for the actual template text; that file is a
  human-readable summary of it (same pattern as
  `docs/bcp_generation_rules.md` for Phase 2).

Safety-relevant rule: `generate_holding_statement_draft` ALWAYS returns
`pre_approved_by_legal=False`, regardless of any caller-supplied value —
this is enforced in code (not just a DB column default) because an
auto-generated draft holding statement must never be mistaken for a
legally-approved message ready for dispatch. See the function docstring.

Conventions reused, unchanged, from bia_engine.py (Phase 1) /
bcp_generator.py (Phase 2) / crisis_management.py (this phase) per the
Phase 3 brief — this module does not reinvent RBAC, audit logging, error
handling, or connection style:

- psycopg (v3), dict-row cursors via bcm_planner.db.get_connection().
- `bia_engine.require_role` for RBAC (same WRITE_ROLES allow-list).
- `bia_engine.log_audit` for append-only audit logging.
- `bia_engine.BCMPlannerError` / `NotFoundError` / `InsufficientRoleError`
  as the shared exception hierarchy.
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

__all__ = [
    "BCMPlannerError",
    "InsufficientRoleError",
    "NotFoundError",
    "create_stakeholder_contact",
    "get_stakeholder_contact",
    "update_stakeholder_contact",
    "list_stakeholder_contacts_by_organization",
    "create_message_bank_entry",
    "get_message_bank_entry",
    "update_message_bank_entry",
    "list_message_bank_entries_by_organization",
    "generate_holding_statement_draft",
    "HOLDING_STATEMENT_TEMPLATES",
]


# ---------------------------------------------------------------------------
# stakeholder_contact_matrices CRUD
# ---------------------------------------------------------------------------


def create_stakeholder_contact(
    conn: psycopg.Connection,
    user_id: UUID | str,
    organization_id: UUID | str,
    stakeholder_group: str,
    contact_person_or_entity: str,
    primary_channel: str,
    backup_channel: Optional[str] = None,
    notification_priority: int = 1,
) -> dict[str, Any]:
    """Creates a stakeholder_contact_matrices row (e.g. Internal Personnel,
    Executive Leadership, Media, Regulators, Key Vendors), scoped to an
    organization.
    """
    require_role(conn, user_id, WRITE_ROLES)
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO stakeholder_contact_matrices
                (organization_id, stakeholder_group, contact_person_or_entity,
                 primary_channel, backup_channel, notification_priority)
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING *
            """,
            (
                str(organization_id), stakeholder_group, contact_person_or_entity,
                primary_channel, backup_channel, notification_priority,
            ),
        )
        row = cur.fetchone()
    log_audit(
        conn, user_id, "CREATE", "stakeholder_contact_matrices", row["contact_id"],
        {"stakeholder_group": stakeholder_group, "contact_person_or_entity": contact_person_or_entity},
        organization_id=organization_id,
    )
    return row


def get_stakeholder_contact(conn: psycopg.Connection, contact_id: UUID | str) -> dict[str, Any]:
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM stakeholder_contact_matrices WHERE contact_id = %s", (str(contact_id),))
        row = cur.fetchone()
    if row is None:
        raise NotFoundError(f"Stakeholder contact {contact_id} not found.")
    return row


def list_stakeholder_contacts_by_organization(conn: psycopg.Connection, organization_id: UUID | str) -> list[dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM stakeholder_contact_matrices WHERE organization_id = %s ORDER BY notification_priority, stakeholder_group",
            (str(organization_id),),
        )
        return cur.fetchall()


def update_stakeholder_contact(
    conn: psycopg.Connection,
    user_id: UUID | str,
    contact_id: UUID | str,
    **fields: Any,
) -> dict[str, Any]:
    """Updates a subset of mutable stakeholder_contact_matrices fields.

    Allowed fields: stakeholder_group, contact_person_or_entity,
    primary_channel, backup_channel, notification_priority.
    """
    require_role(conn, user_id, WRITE_ROLES)
    allowed_fields = {
        "stakeholder_group", "contact_person_or_entity",
        "primary_channel", "backup_channel", "notification_priority",
    }
    unknown = set(fields) - allowed_fields
    if unknown:
        raise BCMPlannerError(f"Unknown/immutable stakeholder_contact_matrices field(s): {', '.join(sorted(unknown))}")
    if not fields:
        raise BCMPlannerError("update_stakeholder_contact called with no fields to update.")

    set_clause = ", ".join(f"{k} = %s" for k in fields)
    params = list(fields.values()) + [str(contact_id)]
    with conn.cursor() as cur:
        cur.execute(
            f"UPDATE stakeholder_contact_matrices SET {set_clause} WHERE contact_id = %s RETURNING *", params
        )
        row = cur.fetchone()
    if row is None:
        raise NotFoundError(f"Stakeholder contact {contact_id} not found.")
    log_audit(conn, user_id, "UPDATE", "stakeholder_contact_matrices", contact_id, fields)
    return row


# ---------------------------------------------------------------------------
# message_bank CRUD
# ---------------------------------------------------------------------------


def create_message_bank_entry(
    conn: psycopg.Connection,
    user_id: UUID | str,
    organization_id: UUID | str,
    scenario_type: str,
    target_audience: str,
    holding_statement_template: str,
    pre_approved_by_legal: bool = False,
    dispatch_channels: Optional[list[str]] = None,
) -> dict[str, Any]:
    """Creates a message_bank row. `pre_approved_by_legal` defaults to
    False and is only ever True if the caller explicitly passes True (e.g.
    after a human has actually run legal sign-off) — this function itself
    does not perform any legal approval; see
    `generate_holding_statement_draft` for the auto-generation path, which
    always forces this flag False regardless of caller input.
    """
    require_role(conn, user_id, WRITE_ROLES)
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO message_bank
                (organization_id, scenario_type, target_audience, holding_statement_template,
                 pre_approved_by_legal, dispatch_channels)
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING *
            """,
            (
                str(organization_id), scenario_type, target_audience, holding_statement_template,
                pre_approved_by_legal, dispatch_channels,
            ),
        )
        row = cur.fetchone()
    log_audit(
        conn, user_id, "CREATE", "message_bank", row["message_id"],
        {"scenario_type": scenario_type, "target_audience": target_audience, "pre_approved_by_legal": pre_approved_by_legal},
        organization_id=organization_id,
    )
    return row


def get_message_bank_entry(conn: psycopg.Connection, message_id: UUID | str) -> dict[str, Any]:
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM message_bank WHERE message_id = %s", (str(message_id),))
        row = cur.fetchone()
    if row is None:
        raise NotFoundError(f"Message bank entry {message_id} not found.")
    return row


def list_message_bank_entries_by_organization(conn: psycopg.Connection, organization_id: UUID | str) -> list[dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM message_bank WHERE organization_id = %s ORDER BY scenario_type, target_audience",
            (str(organization_id),),
        )
        return cur.fetchall()


def update_message_bank_entry(
    conn: psycopg.Connection,
    user_id: UUID | str,
    message_id: UUID | str,
    **fields: Any,
) -> dict[str, Any]:
    """Updates a subset of mutable message_bank fields.

    Allowed fields: scenario_type, target_audience, holding_statement_template,
    pre_approved_by_legal, dispatch_channels.

    Note: setting pre_approved_by_legal=True here is a normal, permitted
    write (this is how a human records that legal actually reviewed and
    approved a message) — the "always False" enforcement only applies to
    the auto-generate draft path (`generate_holding_statement_draft`),
    which never calls this function's caller-supplied True through
    un-reviewed.
    """
    require_role(conn, user_id, WRITE_ROLES)
    allowed_fields = {
        "scenario_type", "target_audience", "holding_statement_template",
        "pre_approved_by_legal", "dispatch_channels",
    }
    unknown = set(fields) - allowed_fields
    if unknown:
        raise BCMPlannerError(f"Unknown/immutable message_bank field(s): {', '.join(sorted(unknown))}")
    if not fields:
        raise BCMPlannerError("update_message_bank_entry called with no fields to update.")

    set_clause = ", ".join(f"{k} = %s" for k in fields)
    params = list(fields.values()) + [str(message_id)]
    with conn.cursor() as cur:
        cur.execute(f"UPDATE message_bank SET {set_clause} WHERE message_id = %s RETURNING *", params)
        row = cur.fetchone()
    if row is None:
        raise NotFoundError(f"Message bank entry {message_id} not found.")
    log_audit(conn, user_id, "UPDATE", "message_bank", message_id, fields)
    return row


# ---------------------------------------------------------------------------
# Rule-based holding statement auto-generation (FR10)
# See docs/crisis_communication_templates.md for the human-readable summary.
# ---------------------------------------------------------------------------

# scenario_type -> holding statement template text, with placeholder tokens
# a human fills in before dispatch: {incident_summary},
# {expected_resolution_time}, {contact_channel}. Every template also
# reminds the reader that this is a DRAFT requiring legal review.
#
# Scenario categories chosen to match the schema comment on
# message_bank.scenario_type in schema/001_core_schema.sql ("Power
# Outage, Cyberattack/DDoS, Public Transit Disruption, Data Breach") plus
# the disruption templates already implied by REQUIREMENTS.md FR11
# ("power outages, cyberattacks, or transit disruptions").
HOLDING_STATEMENT_TEMPLATES: dict[str, str] = {
    "power_outage": (
        "[DRAFT — NOT LEGALLY APPROVED] We are currently experiencing a power outage "
        "affecting {incident_summary}. Our teams are actively working to restore power and "
        "resume normal operations. We expect this to be resolved by {expected_resolution_time}. "
        "For updates, please contact us via {contact_channel}. We apologize for any "
        "inconvenience and appreciate your patience."
    ),
    "cyberattack_ddos": (
        "[DRAFT — NOT LEGALLY APPROVED] We are aware of a cybersecurity incident affecting "
        "{incident_summary}. Our security and IT teams are actively investigating and taking "
        "steps to contain and remediate the issue. We expect normal service to be restored by "
        "{expected_resolution_time}. For further information, please contact {contact_channel}. "
        "We take the security of our systems and data seriously and will provide updates as "
        "they become available."
    ),
    "data_breach": (
        "[DRAFT — NOT LEGALLY APPROVED] We recently identified a data security incident "
        "involving {incident_summary}. We are investigating the scope and impact of this "
        "incident with the support of our security team and will provide further updates by "
        "{expected_resolution_time}. If you have questions or believe you may be affected, "
        "please contact {contact_channel}. NOTE: this template requires legal and regulatory "
        "review before any external dispatch — data breach communications may carry statutory "
        "notification obligations."
    ),
    "public_transit_disruption": (
        "[DRAFT — NOT LEGALLY APPROVED] We are currently experiencing a disruption affecting "
        "{incident_summary}. Our teams are working to restore normal service as quickly and "
        "safely as possible, with resolution expected by {expected_resolution_time}. For "
        "real-time updates and alternative arrangements, please contact {contact_channel}. We "
        "apologize for any inconvenience this may cause to your journey."
    ),
}

# Safe fallback template used for any scenario_type not in the table above,
# so the helper never silently produces an empty/incorrect draft for an
# unrecognized (but still valid, free-text) scenario_type value.
_GENERIC_HOLDING_STATEMENT_TEMPLATE = (
    "[DRAFT — NOT LEGALLY APPROVED] We are currently responding to an incident affecting "
    "{incident_summary}. Our teams are actively assessing the situation and working towards "
    "resolution, expected by {expected_resolution_time}. For further information or support, "
    "please contact {contact_channel}. This is a generic fallback template (scenario_type not "
    "in the pre-built template set — see docs/crisis_communication_templates.md) and should be "
    "reviewed and tailored before any use."
)


def generate_holding_statement_draft(
    scenario_type: str,
    target_audience: str,
    pre_approved_by_legal: bool = False,
) -> dict[str, Any]:
    """Produces a rule-based DRAFT holding statement for a given
    `scenario_type` (matched case-insensitively, spaces/hyphens normalized
    to underscores, against HOLDING_STATEMENT_TEMPLATES — falls back to a
    generic template for unrecognized scenario_types) and
    `target_audience`.

    The returned template contains placeholder tokens
    (`{incident_summary}`, `{expected_resolution_time}`,
    `{contact_channel}`) that a human must fill in before dispatch — this
    is a draft generator, not a live-dispatch tool (no NFR14 broadcast
    integration; see REQUIREMENTS.md scope decision).

    SAFETY-CRITICAL BEHAVIOR: `pre_approved_by_legal` is ALWAYS forced to
    False in the returned dict, regardless of what the caller passes in
    for that parameter. This is deliberate and enforced here in code (not
    merely relying on the DB column default of False) — an auto-generated
    draft must never be represented as legally pre-approved. If a caller
    passes `pre_approved_by_legal=True`, that value is silently discarded
    and replaced with False; callers should never rely on this parameter
    to mark a draft as approved. Legal approval is recorded separately via
    `update_message_bank_entry(..., pre_approved_by_legal=True)` after an
    actual human legal review, on an existing message_bank row — never as
    a side effect of drafting.

    Returns a dict with keys: scenario_type, target_audience,
    holding_statement_template, pre_approved_by_legal (always False),
    dispatch_channels (empty list — dispatch channel selection is a
    separate, explicit CRUD/config decision, not implied by the draft).
    Does not write to the database — this is a pure template-generation
    helper; persist the result via `create_message_bank_entry` if desired.
    """
    normalized = scenario_type.strip().lower().replace(" ", "_").replace("-", "_").replace("/", "_")
    template = HOLDING_STATEMENT_TEMPLATES.get(normalized, _GENERIC_HOLDING_STATEMENT_TEMPLATE)

    return {
        "scenario_type": scenario_type,
        "target_audience": target_audience,
        "holding_statement_template": template,
        # Enforced False no matter what the caller passed — see docstring.
        "pre_approved_by_legal": False,
        "dispatch_channels": [],
    }
