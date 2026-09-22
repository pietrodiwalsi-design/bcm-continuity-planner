"""Pytest suite for the Phase 4 Exercise & Test Planner
(exercise_planner.py + scenario_injects.py + exercise_debrief.py).

Covers, per the Phase 4 brief:
- exercise_programmes and exercises CRUD round-trips.
- get_disruption_scenario_template returns sensible content for at least
  two scenario types, and a documented error for an unknown type.
- scenario_injects CRUD round-trip.
- get_exercise_storyboard returns injects in correct time order, and
  raises a clear error for out-of-order sequence_number vs.
  time_offset_minutes or duplicate sequence_number.
- generate_injects_from_scenario_template produces a plausible
  time-phased set for at least two scenario types.
- exercise_debriefs CRUD round-trip, and a second debrief attempt for the
  same exercise_id raises a clear application-level error.
- capa_action_items CRUD round-trip.
- get_overdue_capa_items correctly identifies overdue vs. on-track items.
- RBAC denies/allows exactly like the existing pattern.
- Audit log rows written for all five new entity types (exercise_programmes,
  exercises, scenario_injects, exercise_debriefs, capa_action_items).

Reuses the same fixtures (`conn`, `org`, `activity`) from tests/conftest.py
that the other test_*.py files use. Requires a running Postgres instance
with schema 001+002 applied. See TESTING.md.
"""

from __future__ import annotations

import datetime
import uuid

import pytest

from bcm_planner import exercise_debrief, exercise_planner, scenario_injects


# ---------------------------------------------------------------------------
# exercise_programmes CRUD round-trip
# ---------------------------------------------------------------------------


def test_exercise_programme_crud_roundtrip(conn, org):
    admin_id = org["admin_user_id"]
    org_id = org["organization"]["organization_id"]

    programme = exercise_planner.create_exercise_programme(
        conn, admin_id, org_id, "2026 BCM Exercise Programme", 2026,
        "Validate BCP/CMP effectiveness across all prioritised activities.",
        approved_budget=15000.00,
    )
    assert programme["title"] == "2026 BCM Exercise Programme"
    assert programme["annual_schedule_year"] == 2026

    fetched = exercise_planner.get_exercise_programme(conn, programme["programme_id"])
    assert fetched["objectives"].startswith("Validate")

    updated = exercise_planner.update_exercise_programme(
        conn, admin_id, programme["programme_id"], approved_budget=20000.00
    )
    assert float(updated["approved_budget"]) == 20000.00

    listed = exercise_planner.list_exercise_programmes_by_organization(conn, org_id)
    assert any(p["programme_id"] == programme["programme_id"] for p in listed)

    with pytest.raises(exercise_planner.BCMPlannerError):
        exercise_planner.update_exercise_programme(conn, admin_id, programme["programme_id"])  # no fields

    with pytest.raises(exercise_planner.BCMPlannerError):
        exercise_planner.update_exercise_programme(conn, admin_id, programme["programme_id"], not_a_real_field="x")

    with pytest.raises(exercise_planner.NotFoundError):
        exercise_planner.get_exercise_programme(conn, str(uuid.uuid4()))


@pytest.fixture()
def programme(conn, org):
    admin_id = org["admin_user_id"]
    org_id = org["organization"]["organization_id"]
    return exercise_planner.create_exercise_programme(
        conn, admin_id, org_id, "Test Programme", 2026, "Test objectives.",
    )


# ---------------------------------------------------------------------------
# exercises CRUD round-trip
# ---------------------------------------------------------------------------


def test_exercise_crud_roundtrip(conn, org, programme):
    admin_id = org["admin_user_id"]

    exercise = exercise_planner.create_exercise(
        conn, admin_id, programme["programme_id"], "scenario_tabletop",
        "Power Outage Tabletop", datetime.date(2026, 10, 1), "Jane Doe",
        "A tabletop exercise simulating a full site power outage.",
    )
    assert exercise["category"] == "scenario_tabletop"
    assert exercise["status"] == "Scheduled"

    fetched = exercise_planner.get_exercise(conn, exercise["exercise_id"])
    assert fetched["lead_facilitator"] == "Jane Doe"

    updated = exercise_planner.update_exercise(conn, admin_id, exercise["exercise_id"], status="Completed")
    assert updated["status"] == "Completed"

    listed = exercise_planner.list_exercises_by_programme(conn, programme["programme_id"])
    assert any(e["exercise_id"] == exercise["exercise_id"] for e in listed)

    with pytest.raises(exercise_planner.NotFoundError):
        exercise_planner.get_exercise(conn, str(uuid.uuid4()))


def test_exercise_create_unknown_programme_raises_not_found(conn, org):
    admin_id = org["admin_user_id"]
    with pytest.raises(exercise_planner.NotFoundError):
        exercise_planner.create_exercise(
            conn, admin_id, str(uuid.uuid4()), "discussion_based", "X",
            datetime.date(2026, 1, 1), "Y", "Z",
        )


def test_exercise_invalid_category_raises(conn, org, programme):
    admin_id = org["admin_user_id"]
    with pytest.raises(Exception):
        exercise_planner.create_exercise(
            conn, admin_id, programme["programme_id"], "not_a_real_category",
            "X", datetime.date(2026, 1, 1), "Y", "Z",
        )
    conn.rollback()


@pytest.fixture()
def exercise(conn, org, programme):
    admin_id = org["admin_user_id"]
    return exercise_planner.create_exercise(
        conn, admin_id, programme["programme_id"], "simulation",
        "Cyberattack Simulation", datetime.date(2026, 11, 1), "Alice Facilitator",
        "A simulation of a ransomware attack affecting core IT systems.",
    )


# ---------------------------------------------------------------------------
# get_disruption_scenario_template
# ---------------------------------------------------------------------------


def test_get_disruption_scenario_template_power_outage():
    template = exercise_planner.get_disruption_scenario_template("power_outage")
    assert template["scenario_type"] == "power_outage"
    assert template["suggested_category"] == "scenario_tabletop"
    assert "power" in template["suggested_scenario_description"].lower()
    assert len(template["suggested_objectives"]) >= 2


def test_get_disruption_scenario_template_cyberattack_normalized_input():
    template = exercise_planner.get_disruption_scenario_template("Cyberattack/DDoS")
    assert template["scenario_type"] == "cyberattack_ddos"
    assert template["suggested_category"] == "simulation"
    assert len(template["suggested_objectives"]) >= 2


def test_get_disruption_scenario_template_key_supplier_failure():
    template = exercise_planner.get_disruption_scenario_template("key-supplier-failure")
    assert template["scenario_type"] == "key_supplier_failure"
    assert "supplier" in template["suggested_scenario_description"].lower()


def test_get_disruption_scenario_template_unknown_type_raises_documented_error():
    with pytest.raises(exercise_planner.BCMPlannerError) as excinfo:
        exercise_planner.get_disruption_scenario_template("alien_invasion")
    # Documented behavior: unlike Phase 3's holding statement generator, this
    # helper does NOT silently fall back to a generic template.
    assert "alien_invasion" in str(excinfo.value)
    assert "power_outage" in str(excinfo.value)  # lists known scenario types


# ---------------------------------------------------------------------------
# scenario_injects CRUD round-trip
# ---------------------------------------------------------------------------


def test_scenario_inject_crud_roundtrip(conn, org, exercise):
    admin_id = org["admin_user_id"]

    inject = scenario_injects.create_scenario_inject(
        conn, admin_id, exercise["exercise_id"], 1, 0,
        "Initial detection alert", "Monitoring raises an anomaly alert.",
        "Simulated Email", "Triage the alert.",
    )
    assert inject["sequence_number"] == 1
    assert inject["time_offset_minutes"] == 0

    fetched = scenario_injects.get_scenario_inject(conn, inject["inject_id"])
    assert fetched["inject_title"] == "Initial detection alert"

    updated = scenario_injects.update_scenario_inject(
        conn, admin_id, inject["inject_id"], expected_team_action="Escalate immediately."
    )
    assert updated["expected_team_action"] == "Escalate immediately."

    listed = scenario_injects.list_scenario_injects_by_exercise(conn, exercise["exercise_id"])
    assert any(i["inject_id"] == inject["inject_id"] for i in listed)

    with pytest.raises(scenario_injects.NotFoundError):
        scenario_injects.get_scenario_inject(conn, str(uuid.uuid4()))


# ---------------------------------------------------------------------------
# get_exercise_storyboard ordering + validation
# ---------------------------------------------------------------------------


def test_get_exercise_storyboard_correct_time_order(conn, org, exercise):
    admin_id = org["admin_user_id"]
    ex_id = exercise["exercise_id"]

    scenario_injects.create_scenario_inject(
        conn, admin_id, ex_id, 2, 30, "Second inject", "Content B", "Facilitator Announcement", "Action B",
    )
    scenario_injects.create_scenario_inject(
        conn, admin_id, ex_id, 1, 0, "First inject", "Content A", "Facilitator Announcement", "Action A",
    )
    scenario_injects.create_scenario_inject(
        conn, admin_id, ex_id, 3, 60, "Third inject", "Content C", "Facilitator Announcement", "Action C",
    )

    storyboard = scenario_injects.get_exercise_storyboard(conn, ex_id)
    injects = storyboard["injects"]
    assert [i["inject_title"] for i in injects] == ["First inject", "Second inject", "Third inject"]
    assert [i["time_offset_minutes"] for i in injects] == [0, 30, 60]


def test_get_exercise_storyboard_empty_for_new_exercise(conn, org, exercise):
    storyboard = scenario_injects.get_exercise_storyboard(conn, exercise["exercise_id"])
    assert storyboard["injects"] == []


def test_get_exercise_storyboard_raises_on_duplicate_sequence_number(conn, org, exercise):
    admin_id = org["admin_user_id"]
    ex_id = exercise["exercise_id"]

    scenario_injects.create_scenario_inject(
        conn, admin_id, ex_id, 1, 0, "First", "A", "Facilitator Announcement", "Action A",
    )
    scenario_injects.create_scenario_inject(
        conn, admin_id, ex_id, 1, 30, "Duplicate sequence", "B", "Facilitator Announcement", "Action B",
    )

    with pytest.raises(scenario_injects.StoryboardValidationError) as excinfo:
        scenario_injects.get_exercise_storyboard(conn, ex_id)
    assert "sequence_number 1" in str(excinfo.value)


def test_get_exercise_storyboard_raises_on_out_of_order_sequence_number(conn, org, exercise):
    admin_id = org["admin_user_id"]
    ex_id = exercise["exercise_id"]

    # time_offset_minutes=0 gets sequence_number=2, but time_offset_minutes=30
    # gets sequence_number=1 -- inconsistent with time order.
    scenario_injects.create_scenario_inject(
        conn, admin_id, ex_id, 2, 0, "Out of order first", "A", "Facilitator Announcement", "Action A",
    )
    scenario_injects.create_scenario_inject(
        conn, admin_id, ex_id, 1, 30, "Out of order second", "B", "Facilitator Announcement", "Action B",
    )

    with pytest.raises(scenario_injects.StoryboardValidationError) as excinfo:
        scenario_injects.get_exercise_storyboard(conn, ex_id)
    assert "out of order" in str(excinfo.value).lower()


# ---------------------------------------------------------------------------
# generate_injects_from_scenario_template
# ---------------------------------------------------------------------------


def test_generate_injects_from_scenario_template_power_outage(conn, org, exercise):
    admin_id = org["admin_user_id"]
    injects = scenario_injects.generate_injects_from_scenario_template(
        conn, admin_id, exercise["exercise_id"], "power_outage"
    )
    assert 3 <= len(injects) <= 5
    offsets = [i["time_offset_minutes"] for i in injects]
    assert offsets == sorted(offsets)
    assert offsets[0] == 0

    # Storyboard should pass validation since generation is time-ordered.
    storyboard = scenario_injects.get_exercise_storyboard(conn, exercise["exercise_id"])
    assert len(storyboard["injects"]) == len(injects)


def test_generate_injects_from_scenario_template_cyberattack_ddos(conn, org, exercise):
    admin_id = org["admin_user_id"]
    injects = scenario_injects.generate_injects_from_scenario_template(
        conn, admin_id, exercise["exercise_id"], "cyberattack_ddos"
    )
    assert 3 <= len(injects) <= 5
    titles = [i["inject_title"].lower() for i in injects]
    assert any("detection" in t for t in titles)


def test_generate_injects_from_scenario_template_unknown_exercise_raises_not_found(conn, org):
    admin_id = org["admin_user_id"]
    with pytest.raises(scenario_injects.NotFoundError):
        scenario_injects.generate_injects_from_scenario_template(
            conn, admin_id, str(uuid.uuid4()), "power_outage"
        )


def test_generate_injects_from_scenario_template_unknown_scenario_type_raises(conn, org, exercise):
    admin_id = org["admin_user_id"]
    with pytest.raises(scenario_injects.BCMPlannerError):
        scenario_injects.generate_injects_from_scenario_template(
            conn, admin_id, exercise["exercise_id"], "alien_invasion"
        )


# ---------------------------------------------------------------------------
# exercise_debriefs CRUD round-trip + duplicate prevention
# ---------------------------------------------------------------------------


def test_exercise_debrief_crud_roundtrip(conn, org, exercise):
    admin_id = org["admin_user_id"]

    debrief = exercise_debrief.create_exercise_debrief(
        conn, admin_id, exercise["exercise_id"],
        "Overall the team responded well but communication delays were noted.",
        strengths_observed="Fast technical containment.",
        weaknesses_observed="Delayed stakeholder notification.",
        opportunities_for_improvement="Pre-draft holding statements earlier.",
        identified_threats_risks="Reliance on a single on-call engineer.",
        overall_rating="Satisfactory",
    )
    assert debrief["overall_rating"] == "Satisfactory"

    fetched = exercise_debrief.get_exercise_debrief(conn, debrief["debrief_id"])
    assert fetched["hot_debrief_summary"].startswith("Overall")

    by_exercise = exercise_debrief.get_exercise_debrief_by_exercise(conn, exercise["exercise_id"])
    assert by_exercise["debrief_id"] == debrief["debrief_id"]

    updated = exercise_debrief.update_exercise_debrief(
        conn, admin_id, debrief["debrief_id"], overall_rating="Excellent"
    )
    assert updated["overall_rating"] == "Excellent"

    with pytest.raises(exercise_debrief.NotFoundError):
        exercise_debrief.get_exercise_debrief(conn, str(uuid.uuid4()))


def test_second_debrief_for_same_exercise_raises_clear_error(conn, org, exercise):
    admin_id = org["admin_user_id"]
    exercise_debrief.create_exercise_debrief(
        conn, admin_id, exercise["exercise_id"], "First debrief summary.",
    )
    with pytest.raises(exercise_debrief.DuplicateDebriefError) as excinfo:
        exercise_debrief.create_exercise_debrief(
            conn, admin_id, exercise["exercise_id"], "Second debrief summary.",
        )
    # Clear application-level error, not a raw DB traceback.
    assert "already has a debrief" in str(excinfo.value)


@pytest.fixture()
def debrief(conn, org, exercise):
    admin_id = org["admin_user_id"]
    return exercise_debrief.create_exercise_debrief(
        conn, admin_id, exercise["exercise_id"], "Debrief for CAPA tests.",
    )


# ---------------------------------------------------------------------------
# capa_action_items CRUD round-trip
# ---------------------------------------------------------------------------


def test_capa_action_item_crud_roundtrip(conn, org, debrief):
    admin_id = org["admin_user_id"]

    item = exercise_debrief.create_capa_action_item(
        conn, admin_id, debrief["debrief_id"],
        "No documented alternate supplier for critical component X.",
        "Identify and contract a backup supplier for component X.",
        "Vendor Management Lead", datetime.date(2026, 12, 31),
    )
    assert item["status"] == "Open"

    fetched = exercise_debrief.get_capa_action_item(conn, item["action_id"])
    assert fetched["assigned_owner"] == "Vendor Management Lead"

    updated = exercise_debrief.update_capa_action_item(
        conn, admin_id, item["action_id"], status="In Progress"
    )
    assert updated["status"] == "In Progress"

    listed = exercise_debrief.list_capa_action_items_by_debrief(conn, debrief["debrief_id"])
    assert any(i["action_id"] == item["action_id"] for i in listed)

    with pytest.raises(exercise_debrief.NotFoundError):
        exercise_debrief.get_capa_action_item(conn, str(uuid.uuid4()))


def test_capa_action_item_unknown_debrief_raises_not_found(conn, org):
    admin_id = org["admin_user_id"]
    with pytest.raises(exercise_debrief.NotFoundError):
        exercise_debrief.create_capa_action_item(
            conn, admin_id, str(uuid.uuid4()), "Gap.", "Fix.", "Owner", datetime.date(2026, 1, 1),
        )


# ---------------------------------------------------------------------------
# get_overdue_capa_items
# ---------------------------------------------------------------------------


def test_get_overdue_capa_items_identifies_overdue_not_ontrack(conn, org, debrief):
    admin_id = org["admin_user_id"]
    org_id = org["organization"]["organization_id"]

    yesterday = datetime.date.today() - datetime.timedelta(days=1)
    next_year = datetime.date.today() + datetime.timedelta(days=365)

    overdue_item = exercise_debrief.create_capa_action_item(
        conn, admin_id, debrief["debrief_id"],
        "Overdue gap.", "Overdue action.", "Owner A", yesterday, status="Open",
    )
    ontrack_item = exercise_debrief.create_capa_action_item(
        conn, admin_id, debrief["debrief_id"],
        "On-track gap.", "On-track action.", "Owner B", next_year, status="Open",
    )
    completed_but_overdue_item = exercise_debrief.create_capa_action_item(
        conn, admin_id, debrief["debrief_id"],
        "Completed gap.", "Completed action.", "Owner C", yesterday,
        status="Completed", completion_date=yesterday,
    )

    overdue = exercise_debrief.get_overdue_capa_items(conn, org_id)
    overdue_ids = {i["action_id"] for i in overdue}

    assert overdue_item["action_id"] in overdue_ids
    assert ontrack_item["action_id"] not in overdue_ids
    assert completed_but_overdue_item["action_id"] not in overdue_ids


def test_get_open_capa_items_includes_not_yet_due_open_items(conn, org, debrief):
    admin_id = org["admin_user_id"]
    org_id = org["organization"]["organization_id"]

    next_year = datetime.date.today() + datetime.timedelta(days=365)
    open_item = exercise_debrief.create_capa_action_item(
        conn, admin_id, debrief["debrief_id"],
        "Future gap.", "Future action.", "Owner D", next_year, status="Open",
    )
    verified_item = exercise_debrief.create_capa_action_item(
        conn, admin_id, debrief["debrief_id"],
        "Verified gap.", "Verified action.", "Owner E", next_year,
        status="Verified", completion_date=datetime.date.today(),
    )

    open_items = exercise_debrief.get_open_capa_items(conn, org_id)
    open_ids = {i["action_id"] for i in open_items}

    assert open_item["action_id"] in open_ids
    assert verified_item["action_id"] not in open_ids


# ---------------------------------------------------------------------------
# RBAC — same allow/deny pattern as Phase 1/2/3
# ---------------------------------------------------------------------------


def test_rbac_denies_insufficient_role_for_exercise_programme_create(conn, org):
    org_id = org["organization"]["organization_id"]
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO application_users (organization_id, full_name, email, role, is_active)
            VALUES (%s, %s, %s, 'viewer', TRUE) RETURNING user_id
            """,
            (org_id, "Viewer User Exercise", f"viewer-ex-{uuid.uuid4()}@example.test"),
        )
        viewer_id = cur.fetchone()["user_id"]

    with pytest.raises(exercise_planner.InsufficientRoleError) as excinfo:
        exercise_planner.create_exercise_programme(
            conn, viewer_id, org_id, "Should Fail", 2026, "Objectives.",
        )
    assert "viewer" in str(excinfo.value)


def test_rbac_allows_sufficient_role_for_exercise_programme_create(conn, org):
    org_id = org["organization"]["organization_id"]
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO application_users (organization_id, full_name, email, role, is_active)
            VALUES (%s, %s, %s, 'bia_assessor', TRUE) RETURNING user_id
            """,
            (org_id, "Assessor User Exercise", f"assessor-ex-{uuid.uuid4()}@example.test"),
        )
        assessor_id = cur.fetchone()["user_id"]

    programme = exercise_planner.create_exercise_programme(
        conn, assessor_id, org_id, "Should Succeed", 2026, "Objectives.",
    )
    assert programme["title"] == "Should Succeed"


def test_rbac_denies_unknown_user_for_exercise_create(conn, org, programme):
    fake_user_id = str(uuid.uuid4())
    with pytest.raises(exercise_planner.InsufficientRoleError):
        exercise_planner.create_exercise(
            conn, fake_user_id, programme["programme_id"], "discussion_based",
            "X", datetime.date(2026, 1, 1), "Y", "Z",
        )


def test_rbac_denies_insufficient_role_for_scenario_inject_create(conn, org, exercise):
    org_id = org["organization"]["organization_id"]
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO application_users (organization_id, full_name, email, role, is_active)
            VALUES (%s, %s, %s, 'viewer', TRUE) RETURNING user_id
            """,
            (org_id, "Viewer User Inject", f"viewer-inj-{uuid.uuid4()}@example.test"),
        )
        viewer_id = cur.fetchone()["user_id"]

    with pytest.raises(scenario_injects.InsufficientRoleError):
        scenario_injects.create_scenario_inject(
            conn, viewer_id, exercise["exercise_id"], 1, 0, "X", "Y", "Z", "W",
        )


def test_rbac_denies_insufficient_role_for_exercise_debrief_create(conn, org, exercise):
    org_id = org["organization"]["organization_id"]
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO application_users (organization_id, full_name, email, role, is_active)
            VALUES (%s, %s, %s, 'viewer', TRUE) RETURNING user_id
            """,
            (org_id, "Viewer User Debrief", f"viewer-deb-{uuid.uuid4()}@example.test"),
        )
        viewer_id = cur.fetchone()["user_id"]

    with pytest.raises(exercise_debrief.InsufficientRoleError):
        exercise_debrief.create_exercise_debrief(conn, viewer_id, exercise["exercise_id"], "Should fail.")


def test_rbac_denies_insufficient_role_for_capa_action_item_create(conn, org, debrief):
    org_id = org["organization"]["organization_id"]
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO application_users (organization_id, full_name, email, role, is_active)
            VALUES (%s, %s, %s, 'viewer', TRUE) RETURNING user_id
            """,
            (org_id, "Viewer User CAPA", f"viewer-capa-{uuid.uuid4()}@example.test"),
        )
        viewer_id = cur.fetchone()["user_id"]

    with pytest.raises(exercise_debrief.InsufficientRoleError):
        exercise_debrief.create_capa_action_item(
            conn, viewer_id, debrief["debrief_id"], "Gap.", "Fix.", "Owner", datetime.date(2026, 1, 1),
        )


# ---------------------------------------------------------------------------
# Audit logging for new entity types
# ---------------------------------------------------------------------------


def test_audit_log_written_for_exercise_programme_create(conn, org):
    admin_id = org["admin_user_id"]
    org_id = org["organization"]["organization_id"]
    programme = exercise_planner.create_exercise_programme(
        conn, admin_id, org_id, "Audited Programme", 2026, "Objectives.",
    )
    with conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM audit_logs WHERE entity_affected = 'exercise_programmes' AND entity_id = %s",
            (str(programme["programme_id"]),),
        )
        rows = cur.fetchall()
    assert len(rows) == 1
    assert rows[0]["action"] == "CREATE"
    assert rows[0]["user_id"] == str(admin_id)


def test_audit_log_written_for_exercise_create(conn, org, programme):
    admin_id = org["admin_user_id"]
    exercise = exercise_planner.create_exercise(
        conn, admin_id, programme["programme_id"], "live", "Audited Exercise",
        datetime.date(2026, 6, 1), "Facilitator", "Description.",
    )
    with conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM audit_logs WHERE entity_affected = 'exercises' AND entity_id = %s",
            (str(exercise["exercise_id"]),),
        )
        rows = cur.fetchall()
    assert len(rows) == 1
    assert rows[0]["action"] == "CREATE"


def test_audit_log_written_for_scenario_inject_create(conn, org, exercise):
    admin_id = org["admin_user_id"]
    inject = scenario_injects.create_scenario_inject(
        conn, admin_id, exercise["exercise_id"], 1, 0, "Audited Inject", "Content", "Email", "Action",
    )
    with conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM audit_logs WHERE entity_affected = 'scenario_injects' AND entity_id = %s",
            (str(inject["inject_id"]),),
        )
        rows = cur.fetchall()
    assert len(rows) == 1
    assert rows[0]["action"] == "CREATE"


def test_audit_log_written_for_exercise_debrief_create(conn, org, exercise):
    admin_id = org["admin_user_id"]
    debrief = exercise_debrief.create_exercise_debrief(
        conn, admin_id, exercise["exercise_id"], "Audited debrief summary.",
    )
    with conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM audit_logs WHERE entity_affected = 'exercise_debriefs' AND entity_id = %s",
            (str(debrief["debrief_id"]),),
        )
        rows = cur.fetchall()
    assert len(rows) == 1
    assert rows[0]["action"] == "CREATE"


def test_audit_log_written_for_capa_action_item_create(conn, org, debrief):
    admin_id = org["admin_user_id"]
    item = exercise_debrief.create_capa_action_item(
        conn, admin_id, debrief["debrief_id"], "Audited gap.", "Audited fix.", "Owner", datetime.date(2026, 1, 1),
    )
    with conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM audit_logs WHERE entity_affected = 'capa_action_items' AND entity_id = %s",
            (str(item["action_id"]),),
        )
        rows = cur.fetchall()
    assert len(rows) == 1
    assert rows[0]["action"] == "CREATE"
