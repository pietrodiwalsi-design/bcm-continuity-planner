"""Pytest suite for the Phase 2 BCP Generator & Return-to-BAU module.

Covers, per the Phase 2 brief:
- bc_plans CRUD round-trip.
- bcp_action_steps CRUD round-trip, step_number > 0 constraint respected.
- Auto-populate from a BIA + recovery strategy produces a plan with
  invocation_criteria referencing the right MTPD/RTO values and at least
  one action step matching the recovery strategy category's template.
- bau_return_procedures CRUD round-trip.
- Auto-generate BAU phases produces the expected standard phase set.
- RBAC denies/allows exactly like the Phase 1 pattern.
- Audit log rows written for the new entity types.

Reuses the same fixtures (`conn`, `org`, `activity`) from
tests/conftest.py that tests/test_bia_engine.py uses. Requires a running
Postgres instance with schema 001+002 applied. See TESTING.md.
"""

from __future__ import annotations

import datetime
import uuid

import pytest

from bcm_planner import bcp_generator, bia_engine


# ---------------------------------------------------------------------------
# Local fixtures: a BIA assessment + selected recovery strategy, built on
# top of the shared `activity`/`org` fixtures from conftest.py.
# ---------------------------------------------------------------------------


@pytest.fixture()
def bia_with_strategy(conn, activity, org):
    """Creates a bia_assessments row + a selected hot_standby recovery
    strategy + one resource dependency, for use by BCP generation tests.
    """
    admin_id = org["admin_user_id"]
    org_id = org["organization"]["organization_id"]

    bia = bia_engine.create_bia_assessment(
        conn, admin_id, activity["activity_id"], "Assessor One",
        datetime.date.today(), mtpd_hours=48, rto_hours=24, rpo_hours=4,
    )
    strategy = bia_engine.create_recovery_strategy(
        conn, admin_id, bia["bia_id"], "hot_standby", "Hot Standby DC",
        "Fully mirrored hot standby datacenter in a secondary region.",
        is_selected_option=True,
    )
    resource = bia_engine.create_resource(
        conn, admin_id, org_id, "technology", "Core Banking App Server",
    )
    bia_engine.link_activity_resource_dependency(
        conn, admin_id, activity["activity_id"], resource["resource_id"],
        minimum_quantity_required=2,
    )
    return {"bia": bia, "strategy": strategy, "activity": activity, "org": org}


# ---------------------------------------------------------------------------
# bc_plans CRUD round-trip
# ---------------------------------------------------------------------------


def test_bc_plan_crud_roundtrip(conn, org):
    admin_id = org["admin_user_id"]
    org_id = org["organization"]["organization_id"]

    plan = bcp_generator.create_bc_plan(
        conn, admin_id, org_id, "tactical", "Payments Processing BCP",
        "Jane Doe", "Invoke when payments processing is disrupted beyond RTO.",
    )
    assert plan["plan_tier"] == "tactical"
    assert plan["status"] == "Draft"
    assert plan["version"] == "1.0"

    fetched = bcp_generator.get_bc_plan(conn, plan["plan_id"])
    assert fetched["plan_title"] == "Payments Processing BCP"

    updated = bcp_generator.update_bc_plan(conn, admin_id, plan["plan_id"], status="Approved", version="1.1")
    assert updated["status"] == "Approved"
    assert updated["version"] == "1.1"

    listed = bcp_generator.list_bc_plans_by_organization(conn, org_id)
    assert any(p["plan_id"] == plan["plan_id"] for p in listed)

    with pytest.raises(bcp_generator.BCMPlannerError):
        bcp_generator.update_bc_plan(conn, admin_id, plan["plan_id"])  # no fields

    with pytest.raises(bcp_generator.BCMPlannerError):
        bcp_generator.update_bc_plan(conn, admin_id, plan["plan_id"], not_a_real_field="x")

    with pytest.raises(bcp_generator.NotFoundError):
        bcp_generator.get_bc_plan(conn, str(uuid.uuid4()))


# ---------------------------------------------------------------------------
# bcp_action_steps CRUD round-trip + step_number > 0 constraint
# ---------------------------------------------------------------------------


def test_bcp_action_step_crud_roundtrip(conn, org):
    admin_id = org["admin_user_id"]
    org_id = org["organization"]["organization_id"]
    plan = bcp_generator.create_bc_plan(
        conn, admin_id, org_id, "operational", "Test Plan", "Owner",
        "Invoke on disruption.",
    )

    step = bcp_generator.create_bcp_action_step(
        conn, admin_id, plan["plan_id"], 1, "IT Ops Lead", "Activate standby",
        "Follow the standby activation runbook.", timeframe_offset_minutes=15,
    )
    assert step["step_number"] == 1

    fetched = bcp_generator.get_bcp_action_step(conn, step["step_id"])
    assert fetched["action_title"] == "Activate standby"

    updated = bcp_generator.update_bcp_action_step(conn, admin_id, step["step_id"], step_number=2)
    assert updated["step_number"] == 2

    steps = bcp_generator.list_bcp_action_steps_by_plan(conn, plan["plan_id"])
    assert len(steps) == 1
    assert steps[0]["step_number"] == 2

    with pytest.raises(bcp_generator.NotFoundError):
        bcp_generator.get_bcp_action_step(conn, str(uuid.uuid4()))


def test_bcp_action_step_step_number_python_precheck_rejects_zero_and_negative(conn, org):
    admin_id = org["admin_user_id"]
    org_id = org["organization"]["organization_id"]
    plan = bcp_generator.create_bc_plan(
        conn, admin_id, org_id, "operational", "Test Plan", "Owner",
        "Invoke on disruption.",
    )
    with pytest.raises(bcp_generator.InvalidStepNumberError):
        bcp_generator.create_bcp_action_step(
            conn, admin_id, plan["plan_id"], 0, "Role", "Title", "Instructions",
        )
    with pytest.raises(bcp_generator.InvalidStepNumberError):
        bcp_generator.create_bcp_action_step(
            conn, admin_id, plan["plan_id"], -1, "Role", "Title", "Instructions",
        )


def test_bcp_action_step_step_number_db_constraint_backstop(conn, org):
    """Confirms the DB CHECK (step_number > 0) constraint is the
    authoritative backstop and is translated into a clear
    InvalidStepNumberError, never a raw psycopg traceback, mirroring the
    RTO<MTPD backstop test pattern in test_bia_engine.py.
    """
    admin_id = org["admin_user_id"]
    org_id = org["organization"]["organization_id"]
    plan = bcp_generator.create_bc_plan(
        conn, admin_id, org_id, "operational", "Test Plan", "Owner",
        "Invoke on disruption.",
    )
    step = bcp_generator.create_bcp_action_step(
        conn, admin_id, plan["plan_id"], 1, "Role", "Title", "Instructions",
    )
    with pytest.raises(bcp_generator.InvalidStepNumberError):
        bcp_generator.update_bcp_action_step(conn, admin_id, step["step_id"], step_number=-5)


def test_update_bcp_action_step_no_fields_raises(conn, org):
    admin_id = org["admin_user_id"]
    org_id = org["organization"]["organization_id"]
    plan = bcp_generator.create_bc_plan(
        conn, admin_id, org_id, "operational", "Test Plan", "Owner", "Invoke on disruption.",
    )
    step = bcp_generator.create_bcp_action_step(
        conn, admin_id, plan["plan_id"], 1, "Role", "Title", "Instructions",
    )
    with pytest.raises(bcp_generator.BCMPlannerError):
        bcp_generator.update_bcp_action_step(conn, admin_id, step["step_id"])


# ---------------------------------------------------------------------------
# Auto-populate BCP draft from BIA + recovery strategy
# ---------------------------------------------------------------------------


def test_generate_bcp_draft_from_bia_invocation_criteria_references_mtpd_rto(conn, bia_with_strategy, org):
    admin_id = org["admin_user_id"]
    bia = bia_with_strategy["bia"]

    result = bcp_generator.generate_bcp_draft_from_bia(conn, admin_id, bia["bia_id"])
    plan = result["plan"]
    steps = result["action_steps"]

    assert plan["status"] == "Draft"
    assert plan["version"] == "0.1-draft"
    assert f"{bia['rto_hours']}h" in plan["invocation_criteria"]
    assert f"{bia['mtpd_hours']}h" in plan["invocation_criteria"]
    assert f"{bia['rpo_hours']}h" in plan["invocation_criteria"]
    assert str(bia["bia_id"]) in plan["invocation_criteria"]

    # hot_standby implies an alternate facility -> description copied over.
    assert plan["alternate_facility_details"] == bia_with_strategy["strategy"]["description"]

    # At least one action step must match the hot_standby category template.
    hot_standby_titles = {t[1] for t in bcp_generator.CATEGORY_ACTION_STEP_TEMPLATES["hot_standby"]}
    generated_titles = {s["action_title"] for s in steps}
    assert hot_standby_titles & generated_titles  # non-empty intersection

    # And at least one step derived from the resource dependency.
    assert any("Core Banking App Server" in s["action_title"] for s in steps)

    # Steps must be numbered sequentially starting at 1.
    step_numbers = sorted(s["step_number"] for s in steps)
    assert step_numbers == list(range(1, len(steps) + 1))


def test_generate_bcp_draft_from_bia_records_provenance(conn, bia_with_strategy, org):
    admin_id = org["admin_user_id"]
    bia = bia_with_strategy["bia"]
    strategy = bia_with_strategy["strategy"]
    activity = bia_with_strategy["activity"]

    result = bcp_generator.generate_bcp_draft_from_bia(conn, admin_id, bia["bia_id"])
    plan_id = result["plan"]["plan_id"]

    provenance = bcp_generator.get_bc_plan_provenance(conn, plan_id)
    assert provenance is not None
    snapshot = provenance["snapshot_json"]
    assert snapshot["source_bia_id"] == str(bia["bia_id"])
    assert snapshot["source_activity_id"] == str(activity["activity_id"])
    assert snapshot["source_recovery_strategy_id"] == str(strategy["strategy_id"])
    assert snapshot["generation_method"] == "rule_based_auto_populate"


def test_generate_bcp_draft_from_bia_no_selected_strategy_raises(conn, activity, org):
    admin_id = org["admin_user_id"]
    bia = bia_engine.create_bia_assessment(
        conn, admin_id, activity["activity_id"], "Assessor Two",
        datetime.date.today(), mtpd_hours=48, rto_hours=24,
    )
    # A strategy exists but is not selected.
    bia_engine.create_recovery_strategy(
        conn, admin_id, bia["bia_id"], "manual_workaround", "Manual Fallback",
        "Manual paper process.", is_selected_option=False,
    )
    with pytest.raises(bcp_generator.NotFoundError):
        bcp_generator.generate_bcp_draft_from_bia(conn, admin_id, bia["bia_id"])


def test_generate_bcp_draft_from_bia_manual_workaround_no_alternate_facility(conn, activity, org):
    """manual_workaround is not in _ALTERNATE_FACILITY_CATEGORIES, so
    alternate_facility_details should stay NULL, and generated steps should
    match the manual_workaround template rather than hot_standby's.
    """
    admin_id = org["admin_user_id"]
    bia = bia_engine.create_bia_assessment(
        conn, admin_id, activity["activity_id"], "Assessor Two",
        datetime.date.today(), mtpd_hours=48, rto_hours=24,
    )
    bia_engine.create_recovery_strategy(
        conn, admin_id, bia["bia_id"], "manual_workaround", "Manual Fallback",
        "Manual paper process.", is_selected_option=True,
    )
    result = bcp_generator.generate_bcp_draft_from_bia(conn, admin_id, bia["bia_id"])
    assert result["plan"]["alternate_facility_details"] is None

    manual_titles = {t[1] for t in bcp_generator.CATEGORY_ACTION_STEP_TEMPLATES["manual_workaround"]}
    generated_titles = {s["action_title"] for s in result["action_steps"]}
    assert manual_titles.issubset(generated_titles)


# ---------------------------------------------------------------------------
# bau_return_procedures CRUD round-trip
# ---------------------------------------------------------------------------


def test_bau_return_procedure_crud_roundtrip(conn, org):
    admin_id = org["admin_user_id"]
    org_id = org["organization"]["organization_id"]
    plan = bcp_generator.create_bc_plan(
        conn, admin_id, org_id, "operational", "Test Plan", "Owner", "Invoke on disruption.",
    )

    procedure = bcp_generator.create_bau_return_procedure(
        conn, admin_id, plan["plan_id"], 1, "Verify primary resource restoration",
        "Primary Resource Restoration", "Primary resources confirmed restored.",
        "Inspect and sign off on restored resources.",
    )
    assert procedure["phase_number"] == 1

    fetched = bcp_generator.get_bau_return_procedure(conn, procedure["bau_procedure_id"])
    assert fetched["title"] == "Verify primary resource restoration"

    updated = bcp_generator.update_bau_return_procedure(
        conn, admin_id, procedure["bau_procedure_id"], title="Updated Title"
    )
    assert updated["title"] == "Updated Title"

    procedures = bcp_generator.list_bau_return_procedures_by_plan(conn, plan["plan_id"])
    assert len(procedures) == 1
    assert procedures[0]["bau_procedure_id"] == procedure["bau_procedure_id"]

    with pytest.raises(bcp_generator.NotFoundError):
        bcp_generator.get_bau_return_procedure(conn, str(uuid.uuid4()))


# ---------------------------------------------------------------------------
# Auto-generate standard BAU return phases
# ---------------------------------------------------------------------------


def test_generate_bau_return_phases_produces_standard_phase_set(conn, org):
    admin_id = org["admin_user_id"]
    org_id = org["organization"]["organization_id"]
    plan = bcp_generator.create_bc_plan(
        conn, admin_id, org_id, "operational", "Test Plan", "Owner", "Invoke on disruption.",
    )

    phases = bcp_generator.generate_bau_return_phases(conn, admin_id, plan["plan_id"])
    assert len(phases) == 4

    expected_titles = [
        "Verify primary resource restoration",
        "Parallel run / validation",
        "Cutover to primary",
        "Post-incident review handoff",
    ]
    actual_titles = [p["title"] for p in sorted(phases, key=lambda p: p["phase_number"])]
    assert actual_titles == expected_titles

    phase_numbers = sorted(p["phase_number"] for p in phases)
    assert phase_numbers == [1, 2, 3, 4]

    listed = bcp_generator.list_bau_return_procedures_by_plan(conn, plan["plan_id"])
    assert len(listed) == 4


def test_generate_bau_return_phases_unknown_plan_raises(conn, org):
    admin_id = org["admin_user_id"]
    with pytest.raises(bcp_generator.NotFoundError):
        bcp_generator.generate_bau_return_phases(conn, admin_id, str(uuid.uuid4()))


# ---------------------------------------------------------------------------
# RBAC — same allow/deny pattern as test_bia_engine.py
# ---------------------------------------------------------------------------


def test_rbac_denies_insufficient_role_for_bc_plan_create(conn, org):
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

    with pytest.raises(bcp_generator.InsufficientRoleError) as excinfo:
        bcp_generator.create_bc_plan(
            conn, viewer_id, org_id, "operational", "Should Fail", "Owner", "Invoke."
        )
    assert "viewer" in str(excinfo.value)


def test_rbac_allows_sufficient_role_for_bc_plan_create(conn, org):
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

    plan = bcp_generator.create_bc_plan(
        conn, assessor_id, org_id, "operational", "Should Succeed", "Owner", "Invoke."
    )
    assert plan["plan_title"] == "Should Succeed"


def test_rbac_denies_unknown_user_for_bc_plan_create(conn, org):
    org_id = org["organization"]["organization_id"]
    fake_user_id = str(uuid.uuid4())
    with pytest.raises(bcp_generator.InsufficientRoleError):
        bcp_generator.create_bc_plan(
            conn, fake_user_id, org_id, "operational", "Should Fail", "Owner", "Invoke."
        )


def test_rbac_denies_insufficient_role_for_generate_bcp_draft(conn, bia_with_strategy, org):
    org_id = org["organization"]["organization_id"]
    bia = bia_with_strategy["bia"]
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO application_users (organization_id, full_name, email, role, is_active)
            VALUES (%s, %s, %s, 'viewer', TRUE) RETURNING user_id
            """,
            (org_id, "Viewer User Two", f"viewer2-{uuid.uuid4()}@example.test"),
        )
        viewer_id = cur.fetchone()["user_id"]

    with pytest.raises(bcp_generator.InsufficientRoleError):
        bcp_generator.generate_bcp_draft_from_bia(conn, viewer_id, bia["bia_id"])


# ---------------------------------------------------------------------------
# Audit logging for new entity types
# ---------------------------------------------------------------------------


def test_audit_log_written_for_bc_plan_create(conn, org):
    admin_id = org["admin_user_id"]
    org_id = org["organization"]["organization_id"]
    plan = bcp_generator.create_bc_plan(
        conn, admin_id, org_id, "operational", "Audited Plan", "Owner", "Invoke."
    )
    with conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM audit_logs WHERE entity_affected = 'bc_plans' AND entity_id = %s",
            (str(plan["plan_id"]),),
        )
        rows = cur.fetchall()
    assert len(rows) == 1
    assert rows[0]["action"] == "CREATE"
    assert rows[0]["user_id"] == str(admin_id)


def test_audit_log_written_for_bcp_action_step_create(conn, org):
    admin_id = org["admin_user_id"]
    org_id = org["organization"]["organization_id"]
    plan = bcp_generator.create_bc_plan(
        conn, admin_id, org_id, "operational", "Test Plan", "Owner", "Invoke."
    )
    step = bcp_generator.create_bcp_action_step(
        conn, admin_id, plan["plan_id"], 1, "Role", "Title", "Instructions",
    )
    with conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM audit_logs WHERE entity_affected = 'bcp_action_steps' AND entity_id = %s",
            (str(step["step_id"]),),
        )
        rows = cur.fetchall()
    assert len(rows) == 1
    assert rows[0]["action"] == "CREATE"


def test_audit_log_written_for_bau_return_procedure_create(conn, org):
    admin_id = org["admin_user_id"]
    org_id = org["organization"]["organization_id"]
    plan = bcp_generator.create_bc_plan(
        conn, admin_id, org_id, "operational", "Test Plan", "Owner", "Invoke."
    )
    procedure = bcp_generator.create_bau_return_procedure(
        conn, admin_id, plan["plan_id"], 1, "Phase One", "Restoration Type",
        "Validation criteria.", "Action steps.",
    )
    with conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM audit_logs WHERE entity_affected = 'bau_return_procedures' AND entity_id = %s",
            (str(procedure["bau_procedure_id"]),),
        )
        rows = cur.fetchall()
    assert len(rows) == 1
    assert rows[0]["action"] == "CREATE"


def test_audit_log_written_for_generate_bcp_draft(conn, bia_with_strategy, org):
    admin_id = org["admin_user_id"]
    bia = bia_with_strategy["bia"]
    result = bcp_generator.generate_bcp_draft_from_bia(conn, admin_id, bia["bia_id"])
    plan_id = result["plan"]["plan_id"]
    with conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM audit_logs WHERE entity_affected = 'bc_plans' AND entity_id = %s AND action = 'CREATE'",
            (str(plan_id),),
        )
        rows = cur.fetchall()
    # One row from create_bc_plan itself (inside generate_bcp_draft_from_bia),
    # plus one summary row logged by generate_bcp_draft_from_bia itself.
    assert len(rows) == 2


def test_audit_log_written_for_generate_bau_return_phases(conn, org):
    admin_id = org["admin_user_id"]
    org_id = org["organization"]["organization_id"]
    plan = bcp_generator.create_bc_plan(
        conn, admin_id, org_id, "operational", "Test Plan", "Owner", "Invoke."
    )
    bcp_generator.generate_bau_return_phases(conn, admin_id, plan["plan_id"])
    with conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM audit_logs WHERE entity_affected = 'bau_return_procedures' AND entity_id = %s AND action = 'CREATE'",
            (str(plan["plan_id"]),),
        )
        rows = cur.fetchall()
    # One summary row logged by generate_bau_return_phases (entity_id = plan_id).
    assert len(rows) == 1
