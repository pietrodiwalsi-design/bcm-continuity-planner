"""Pytest suite for the Phase 3 Crisis Management module
(crisis_management.py + crisis_communications.py).

Covers, per the Phase 3 brief:
- cmt_roles CRUD round-trip.
- escalation_triggers CRUD round-trip.
- get_escalation_path_for_severity returns the correct CMT roles for a low
  severity vs. a high severity case, demonstrating the documented
  heuristic difference.
- stakeholder_contact_matrices CRUD round-trip.
- message_bank CRUD round-trip.
- generate_holding_statement_draft produces templates with the expected
  placeholder tokens for multiple scenario_types, and always forces
  pre_approved_by_legal=False regardless of caller input.
- RBAC denies/allows exactly like the existing Phase 1/2 pattern.
- Audit log rows written for all four new entity types.

Reuses the same fixtures (`conn`, `org`, `activity`) from tests/conftest.py
that test_bia_engine.py / test_bcp_generator.py use. Requires a running
Postgres instance with schema 001+002 applied. See TESTING.md.
"""

from __future__ import annotations

import uuid

import pytest

from bcm_planner import crisis_communications, crisis_management


# ---------------------------------------------------------------------------
# cmt_roles CRUD round-trip
# ---------------------------------------------------------------------------


def test_cmt_role_crud_roundtrip(conn, org):
    admin_id = org["admin_user_id"]
    org_id = org["organization"]["organization_id"]

    role = crisis_management.create_cmt_role(
        conn, admin_id, org_id, "Crisis Management Team Leader",
        "Jane Doe", "+31 6 1234 5678", "jane.doe@example.test",
        "Overall crisis response coordination and CMT convening authority.",
    )
    assert role["role_name"] == "Crisis Management Team Leader"
    assert role["primary_assignee_name"] == "Jane Doe"

    fetched = crisis_management.get_cmt_role(conn, role["role_id"])
    assert fetched["primary_assignee_email"] == "jane.doe@example.test"

    updated = crisis_management.update_cmt_role(
        conn, admin_id, role["role_id"], alternate_assignee_name="John Smith",
        alternate_assignee_phone="+31 6 8765 4321",
    )
    assert updated["alternate_assignee_name"] == "John Smith"

    listed = crisis_management.list_cmt_roles_by_organization(conn, org_id)
    assert any(r["role_id"] == role["role_id"] for r in listed)

    with pytest.raises(crisis_management.BCMPlannerError):
        crisis_management.update_cmt_role(conn, admin_id, role["role_id"])  # no fields

    with pytest.raises(crisis_management.BCMPlannerError):
        crisis_management.update_cmt_role(conn, admin_id, role["role_id"], not_a_real_field="x")

    with pytest.raises(crisis_management.NotFoundError):
        crisis_management.get_cmt_role(conn, str(uuid.uuid4()))


# ---------------------------------------------------------------------------
# escalation_triggers CRUD round-trip
# ---------------------------------------------------------------------------


def test_escalation_trigger_crud_roundtrip(conn, org):
    admin_id = org["admin_user_id"]
    org_id = org["organization"]["organization_id"]

    trigger = crisis_management.create_escalation_trigger(
        conn, admin_id, org_id, "major",
        "Disruption affecting a prioritised activity beyond its RTO.",
        30, "Notify full Crisis Management Team and convene within 30 minutes.",
    )
    assert trigger["severity_level"] == "major"
    assert trigger["notification_timeframe_minutes"] == 30

    fetched = crisis_management.get_escalation_trigger(conn, trigger["trigger_id"])
    assert fetched["incident_condition"].startswith("Disruption")

    updated = crisis_management.update_escalation_trigger(
        conn, admin_id, trigger["trigger_id"], notification_timeframe_minutes=15
    )
    assert updated["notification_timeframe_minutes"] == 15

    listed = crisis_management.list_escalation_triggers_by_organization(conn, org_id)
    assert any(t["trigger_id"] == trigger["trigger_id"] for t in listed)

    with pytest.raises(crisis_management.NotFoundError):
        crisis_management.get_escalation_trigger(conn, str(uuid.uuid4()))


def test_escalation_trigger_invalid_severity_level_raises(conn, org):
    """severity_level must be a valid severity_level_enum value; the DB
    rejects anything else with a clear error (no Python pre-check needed
    here since there is no cross-field business rule like RTO<MTPD --
    the enum itself is the constraint)."""
    admin_id = org["admin_user_id"]
    org_id = org["organization"]["organization_id"]
    with pytest.raises(Exception):
        crisis_management.create_escalation_trigger(
            conn, admin_id, org_id, "not_a_real_severity",
            "Bad condition.", 10, "N/A",
        )
    conn.rollback()


# ---------------------------------------------------------------------------
# get_escalation_path_for_severity heuristic
# ---------------------------------------------------------------------------


@pytest.fixture()
def cmt_roster(conn, org):
    """Creates a small, deliberately mixed CMT roster: some roles that
    should match the first-response heuristic (IT/Security, Operations)
    and some that should not (Legal Counsel, Spokesperson) -- so the low
    vs. high severity test can demonstrate a real difference.
    """
    admin_id = org["admin_user_id"]
    org_id = org["organization"]["organization_id"]

    it_security = crisis_management.create_cmt_role(
        conn, admin_id, org_id, "IT/Security Head", "Alice IT", "+1 555 0100",
        "alice.it@example.test", "Leads technical/security incident response and operational restoration.",
    )
    ops_lead = crisis_management.create_cmt_role(
        conn, admin_id, org_id, "Operations Duty Manager", "Bob Ops", "+1 555 0101",
        "bob.ops@example.test", "First responder for operational incidents; coordinates facilities and engineering.",
    )
    legal = crisis_management.create_cmt_role(
        conn, admin_id, org_id, "Legal Counsel", "Carol Legal", "+1 555 0102",
        "carol.legal@example.test", "Provides legal risk assessment and regulatory notification guidance.",
    )
    spokesperson = crisis_management.create_cmt_role(
        conn, admin_id, org_id, "Spokesperson", "Dave Comms", "+1 555 0103",
        "dave.comms@example.test", "External media liaison and public statement authority.",
    )
    return {
        "org_id": org_id, "admin_id": admin_id,
        "it_security": it_security, "ops_lead": ops_lead,
        "legal": legal, "spokesperson": spokesperson,
    }


def test_get_escalation_path_for_severity_low_severity_only_first_response_roles(conn, cmt_roster):
    admin_id = cmt_roster["admin_id"]
    org_id = cmt_roster["org_id"]

    crisis_management.create_escalation_trigger(
        conn, admin_id, org_id, "minor",
        "A single non-prioritised activity is disrupted.",
        120, "Notify operational/first-response roles only; monitor.",
    )

    path = crisis_management.get_escalation_path_for_severity(conn, org_id, "minor")
    assert path["severity_level"] == "minor"
    assert len(path["triggers"]) == 1

    notified_role_ids = {r["role_id"] for r in path["notified_roles"]}
    assert cmt_roster["it_security"]["role_id"] in notified_role_ids
    assert cmt_roster["ops_lead"]["role_id"] in notified_role_ids
    # Legal Counsel and Spokesperson should NOT be notified for a minor severity.
    assert cmt_roster["legal"]["role_id"] not in notified_role_ids
    assert cmt_roster["spokesperson"]["role_id"] not in notified_role_ids


def test_get_escalation_path_for_severity_high_severity_notifies_all_roles(conn, cmt_roster):
    admin_id = cmt_roster["admin_id"]
    org_id = cmt_roster["org_id"]

    crisis_management.create_escalation_trigger(
        conn, admin_id, org_id, "catastrophic",
        "Multiple prioritised activities disrupted beyond MTPD.",
        5, "Convene full Crisis Management Team immediately.",
    )

    path = crisis_management.get_escalation_path_for_severity(conn, org_id, "catastrophic")
    assert path["severity_level"] == "catastrophic"
    assert len(path["triggers"]) == 1

    notified_role_ids = {r["role_id"] for r in path["notified_roles"]}
    # A catastrophic incident must notify the ENTIRE CMT, including
    # Legal Counsel and Spokesperson, unlike the low-severity case above.
    assert cmt_roster["it_security"]["role_id"] in notified_role_ids
    assert cmt_roster["ops_lead"]["role_id"] in notified_role_ids
    assert cmt_roster["legal"]["role_id"] in notified_role_ids
    assert cmt_roster["spokesperson"]["role_id"] in notified_role_ids
    assert len(notified_role_ids) == 4


def test_get_escalation_path_for_severity_no_matching_trigger_returns_empty_triggers(conn, cmt_roster):
    org_id = cmt_roster["org_id"]
    path = crisis_management.get_escalation_path_for_severity(conn, org_id, "moderate")
    assert path["triggers"] == []
    # notified_roles should still be computed (moderate is a low-severity
    # bucket) even though no trigger row exists yet for it.
    assert isinstance(path["notified_roles"], list)


# ---------------------------------------------------------------------------
# stakeholder_contact_matrices CRUD round-trip
# ---------------------------------------------------------------------------


def test_stakeholder_contact_crud_roundtrip(conn, org):
    admin_id = org["admin_user_id"]
    org_id = org["organization"]["organization_id"]

    contact = crisis_communications.create_stakeholder_contact(
        conn, admin_id, org_id, "Executive Leadership", "CEO Office",
        "Phone", backup_channel="Email", notification_priority=1,
    )
    assert contact["stakeholder_group"] == "Executive Leadership"
    assert contact["notification_priority"] == 1

    fetched = crisis_communications.get_stakeholder_contact(conn, contact["contact_id"])
    assert fetched["contact_person_or_entity"] == "CEO Office"

    updated = crisis_communications.update_stakeholder_contact(
        conn, admin_id, contact["contact_id"], notification_priority=2
    )
    assert updated["notification_priority"] == 2

    listed = crisis_communications.list_stakeholder_contacts_by_organization(conn, org_id)
    assert any(c["contact_id"] == contact["contact_id"] for c in listed)

    with pytest.raises(crisis_communications.BCMPlannerError):
        crisis_communications.update_stakeholder_contact(conn, admin_id, contact["contact_id"])  # no fields

    with pytest.raises(crisis_communications.NotFoundError):
        crisis_communications.get_stakeholder_contact(conn, str(uuid.uuid4()))


# ---------------------------------------------------------------------------
# message_bank CRUD round-trip
# ---------------------------------------------------------------------------


def test_message_bank_crud_roundtrip(conn, org):
    admin_id = org["admin_user_id"]
    org_id = org["organization"]["organization_id"]

    entry = crisis_communications.create_message_bank_entry(
        conn, admin_id, org_id, "Power Outage", "General Public",
        "We are experiencing a power outage affecting {incident_summary}.",
        pre_approved_by_legal=False, dispatch_channels=["SMS", "Email"],
    )
    assert entry["scenario_type"] == "Power Outage"
    assert entry["pre_approved_by_legal"] is False
    assert entry["dispatch_channels"] == ["SMS", "Email"]

    fetched = crisis_communications.get_message_bank_entry(conn, entry["message_id"])
    assert fetched["target_audience"] == "General Public"

    # A human legal reviewer approving an existing draft is a normal,
    # explicit write -- not the safety-critical auto-generate path.
    updated = crisis_communications.update_message_bank_entry(
        conn, admin_id, entry["message_id"], pre_approved_by_legal=True
    )
    assert updated["pre_approved_by_legal"] is True

    listed = crisis_communications.list_message_bank_entries_by_organization(conn, org_id)
    assert any(e["message_id"] == entry["message_id"] for e in listed)

    with pytest.raises(crisis_communications.NotFoundError):
        crisis_communications.get_message_bank_entry(conn, str(uuid.uuid4()))


# ---------------------------------------------------------------------------
# generate_holding_statement_draft
# ---------------------------------------------------------------------------


def test_generate_holding_statement_draft_power_outage_contains_placeholders(conn):
    draft = crisis_communications.generate_holding_statement_draft("power_outage", "General Public")
    template = draft["holding_statement_template"]
    assert "{incident_summary}" in template
    assert "{expected_resolution_time}" in template
    assert "{contact_channel}" in template
    assert draft["pre_approved_by_legal"] is False


def test_generate_holding_statement_draft_data_breach_contains_placeholders(conn):
    draft = crisis_communications.generate_holding_statement_draft("Data Breach", "Affected Customers")
    template = draft["holding_statement_template"]
    assert "{incident_summary}" in template
    assert "{expected_resolution_time}" in template
    assert "{contact_channel}" in template
    # Data breach template must exist and differ from the power outage one.
    power_draft = crisis_communications.generate_holding_statement_draft("power_outage", "General Public")
    assert template != power_draft["holding_statement_template"]


def test_generate_holding_statement_draft_scenario_type_normalization(conn):
    """'Data Breach', 'data-breach', and 'data_breach' should all resolve
    to the same template (case/space/hyphen normalization)."""
    a = crisis_communications.generate_holding_statement_draft("Data Breach", "X")
    b = crisis_communications.generate_holding_statement_draft("data-breach", "X")
    c = crisis_communications.generate_holding_statement_draft("data_breach", "X")
    assert a["holding_statement_template"] == b["holding_statement_template"] == c["holding_statement_template"]


def test_generate_holding_statement_draft_unknown_scenario_falls_back_to_generic(conn):
    draft = crisis_communications.generate_holding_statement_draft("alien_invasion", "General Public")
    template = draft["holding_statement_template"]
    assert "{incident_summary}" in template
    assert "{expected_resolution_time}" in template
    assert "{contact_channel}" in template
    assert draft["pre_approved_by_legal"] is False


def test_generate_holding_statement_draft_always_forces_pre_approved_false(conn):
    """Safety-critical: even if a caller passes pre_approved_by_legal=True,
    the returned dict must always have it forced to False."""
    draft = crisis_communications.generate_holding_statement_draft(
        "cyberattack_ddos", "Media", pre_approved_by_legal=True
    )
    assert draft["pre_approved_by_legal"] is False


# ---------------------------------------------------------------------------
# RBAC -- same allow/deny pattern as Phase 1/2
# ---------------------------------------------------------------------------


def test_rbac_denies_insufficient_role_for_cmt_role_create(conn, org):
    org_id = org["organization"]["organization_id"]
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO application_users (organization_id, full_name, email, role, is_active)
            VALUES (%s, %s, %s, 'viewer', TRUE) RETURNING user_id
            """,
            (org_id, "Viewer User", f"viewer-{uuid.uuid4()}@example.test"),
        )
        viewer_id = cur.fetchone()["user_id"]

    with pytest.raises(crisis_management.InsufficientRoleError) as excinfo:
        crisis_management.create_cmt_role(
            conn, viewer_id, org_id, "Should Fail", "N", "P", "e@example.test", "R",
        )
    assert "viewer" in str(excinfo.value)


def test_rbac_allows_sufficient_role_for_cmt_role_create(conn, org):
    org_id = org["organization"]["organization_id"]
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO application_users (organization_id, full_name, email, role, is_active)
            VALUES (%s, %s, %s, 'bia_assessor', TRUE) RETURNING user_id
            """,
            (org_id, "Assessor User", f"assessor-{uuid.uuid4()}@example.test"),
        )
        assessor_id = cur.fetchone()["user_id"]

    role = crisis_management.create_cmt_role(
        conn, assessor_id, org_id, "Should Succeed", "N", "P", "e@example.test", "R",
    )
    assert role["role_name"] == "Should Succeed"


def test_rbac_denies_unknown_user_for_escalation_trigger_create(conn, org):
    org_id = org["organization"]["organization_id"]
    fake_user_id = str(uuid.uuid4())
    with pytest.raises(crisis_management.InsufficientRoleError):
        crisis_management.create_escalation_trigger(
            conn, fake_user_id, org_id, "minor", "Cond.", 60, "Action.",
        )


def test_rbac_denies_insufficient_role_for_stakeholder_contact_create(conn, org):
    org_id = org["organization"]["organization_id"]
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO application_users (organization_id, full_name, email, role, is_active)
            VALUES (%s, %s, %s, 'viewer', TRUE) RETURNING user_id
            """,
            (org_id, "Viewer User Two", f"viewer2-{uuid.uuid4()}@example.test"),
        )
        viewer_id = cur.fetchone()["user_id"]

    with pytest.raises(crisis_communications.InsufficientRoleError):
        crisis_communications.create_stakeholder_contact(
            conn, viewer_id, org_id, "Media", "Press Office", "Email",
        )


def test_rbac_denies_insufficient_role_for_message_bank_create(conn, org):
    org_id = org["organization"]["organization_id"]
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO application_users (organization_id, full_name, email, role, is_active)
            VALUES (%s, %s, %s, 'viewer', TRUE) RETURNING user_id
            """,
            (org_id, "Viewer User Three", f"viewer3-{uuid.uuid4()}@example.test"),
        )
        viewer_id = cur.fetchone()["user_id"]

    with pytest.raises(crisis_communications.InsufficientRoleError):
        crisis_communications.create_message_bank_entry(
            conn, viewer_id, org_id, "Power Outage", "Public", "Template text.",
        )


# ---------------------------------------------------------------------------
# Audit logging for new entity types
# ---------------------------------------------------------------------------


def test_audit_log_written_for_cmt_role_create(conn, org):
    admin_id = org["admin_user_id"]
    org_id = org["organization"]["organization_id"]
    role = crisis_management.create_cmt_role(
        conn, admin_id, org_id, "Audited Role", "N", "P", "e@example.test", "R",
    )
    with conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM audit_logs WHERE entity_affected = 'cmt_roles' AND entity_id = %s",
            (str(role["role_id"]),),
        )
        rows = cur.fetchall()
    assert len(rows) == 1
    assert rows[0]["action"] == "CREATE"
    assert rows[0]["user_id"] == str(admin_id)


def test_audit_log_written_for_escalation_trigger_create(conn, org):
    admin_id = org["admin_user_id"]
    org_id = org["organization"]["organization_id"]
    trigger = crisis_management.create_escalation_trigger(
        conn, admin_id, org_id, "moderate", "Cond.", 60, "Action.",
    )
    with conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM audit_logs WHERE entity_affected = 'escalation_triggers' AND entity_id = %s",
            (str(trigger["trigger_id"]),),
        )
        rows = cur.fetchall()
    assert len(rows) == 1
    assert rows[0]["action"] == "CREATE"


def test_audit_log_written_for_stakeholder_contact_create(conn, org):
    admin_id = org["admin_user_id"]
    org_id = org["organization"]["organization_id"]
    contact = crisis_communications.create_stakeholder_contact(
        conn, admin_id, org_id, "Key Vendors", "Vendor Co", "Email",
    )
    with conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM audit_logs WHERE entity_affected = 'stakeholder_contact_matrices' AND entity_id = %s",
            (str(contact["contact_id"]),),
        )
        rows = cur.fetchall()
    assert len(rows) == 1
    assert rows[0]["action"] == "CREATE"


def test_audit_log_written_for_message_bank_create(conn, org):
    admin_id = org["admin_user_id"]
    org_id = org["organization"]["organization_id"]
    entry = crisis_communications.create_message_bank_entry(
        conn, admin_id, org_id, "Cyberattack/DDoS", "Public", "Template text.",
    )
    with conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM audit_logs WHERE entity_affected = 'message_bank' AND entity_id = %s",
            (str(entry["message_id"]),),
        )
        rows = cur.fetchall()
    assert len(rows) == 1
    assert rows[0]["action"] == "CREATE"
