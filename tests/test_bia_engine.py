"""Pytest suite for the Phase 1 BIA Engine.

Covers, per the Phase 1 brief:
- RTO < MTPD constraint enforcement (both Python pre-check and DB CHECK
  constraint translation).
- Hierarchy CRUD round-trips (parent-child activities).
- Gap analysis gap_hours computation.
- Recovery strategy "only one selected per bia_id" logic.
- RBAC helper allow/deny behavior.

Requires a running Postgres instance with schema 001+002 applied. See
TESTING.md for how to run this suite.
"""

from __future__ import annotations

import datetime
import uuid

import psycopg
import pytest

from bcm_planner import bia_engine


# ---------------------------------------------------------------------------
# RTO < MTPD enforcement
# ---------------------------------------------------------------------------


def test_rto_less_than_mtpd_python_precheck_rejects_violation(conn, activity, org):
    """The Python pre-check should raise before any DB round-trip when RTO >= MTPD."""
    admin_id = org["admin_user_id"]
    with pytest.raises(bia_engine.RTOConstraintViolation) as excinfo:
        bia_engine.create_bia_assessment(
            conn, admin_id, activity["activity_id"], "Assessor One",
            datetime.date.today(), mtpd_hours=24, rto_hours=24,  # equal -> violation
        )
    assert "RTO" in str(excinfo.value)
    assert "MTPD" in str(excinfo.value)


def test_rto_less_than_mtpd_db_constraint_backstop(conn, activity, org):
    """Even if the Python pre-check were bypassed, the DB CHECK constraint
    must still reject rto_hours >= mtpd_hours, and the resulting error must
    be a clear BCMPlannerError, not a raw psycopg traceback.
    """
    admin_id = org["admin_user_id"]
    with pytest.raises(bia_engine.RTOConstraintViolation) as excinfo:
        bia_engine.create_bia_assessment(
            conn, admin_id, activity["activity_id"], "Assessor One",
            datetime.date.today(), mtpd_hours=10, rto_hours=50,  # RTO > MTPD -> violation
        )
    assert not isinstance(excinfo.value, psycopg.Error)


def test_rto_less_than_mtpd_allows_valid_values(conn, activity, org):
    admin_id = org["admin_user_id"]
    bia = bia_engine.create_bia_assessment(
        conn, admin_id, activity["activity_id"], "Assessor One",
        datetime.date.today(), mtpd_hours=48, rto_hours=24, rpo_hours=4,
    )
    assert bia["mtpd_hours"] == 48
    assert bia["rto_hours"] == 24


def test_update_bia_assessment_revalidates_rto_mtpd(conn, activity, org):
    admin_id = org["admin_user_id"]
    bia = bia_engine.create_bia_assessment(
        conn, admin_id, activity["activity_id"], "Assessor One",
        datetime.date.today(), mtpd_hours=48, rto_hours=24,
    )
    with pytest.raises(bia_engine.RTOConstraintViolation):
        bia_engine.update_bia_assessment(conn, admin_id, bia["bia_id"], rto_hours=60)

    updated = bia_engine.update_bia_assessment(conn, admin_id, bia["bia_id"], rto_hours=12)
    assert updated["rto_hours"] == 12


# ---------------------------------------------------------------------------
# Hierarchy CRUD round-trip (parent-child activities)
# ---------------------------------------------------------------------------


def test_hierarchy_crud_roundtrip_with_parent_child_activities(conn, org):
    admin_id = org["admin_user_id"]
    org_id = org["organization"]["organization_id"]

    unit = bia_engine.create_business_unit(conn, admin_id, org_id, "Ops Unit")
    fetched_unit = bia_engine.get_business_unit(conn, unit["unit_id"])
    assert fetched_unit["name"] == "Ops Unit"

    product = bia_engine.create_product_service(conn, admin_id, org_id, "Core Banking Product")
    process = bia_engine.create_business_process(
        conn, admin_id, unit["unit_id"], "Payments Processing", "Jane Doe",
        product_service_id=product["product_service_id"],
    )
    fetched_process = bia_engine.get_business_process(conn, process["process_id"])
    assert fetched_process["product_service_id"] == product["product_service_id"]

    parent_activity = bia_engine.create_activity(conn, admin_id, process["process_id"], "Parent Activity", "Owner A")
    child_activity = bia_engine.create_activity(
        conn, admin_id, process["process_id"], "Child Activity", "Owner B",
        parent_activity_id=parent_activity["activity_id"],
    )

    fetched_child = bia_engine.get_activity(conn, child_activity["activity_id"])
    assert fetched_child["parent_activity_id"] == parent_activity["activity_id"]

    children = bia_engine.list_child_activities(conn, parent_activity["activity_id"])
    assert len(children) == 1
    assert children[0]["activity_id"] == child_activity["activity_id"]

    tree = bia_engine.get_activity_tree(conn, process["process_id"])
    root_ids = {str(node["activity_id"]) for node in tree}
    assert str(parent_activity["activity_id"]) in root_ids
    root_node = next(n for n in tree if str(n["activity_id"]) == str(parent_activity["activity_id"]))
    assert len(root_node["children"]) == 1
    assert str(root_node["children"][0]["activity_id"]) == str(child_activity["activity_id"])


# ---------------------------------------------------------------------------
# Gap analysis
# ---------------------------------------------------------------------------


def test_gap_analysis_gap_hours_computed_correctly(conn, activity, org):
    admin_id = org["admin_user_id"]
    bia = bia_engine.create_bia_assessment(
        conn, admin_id, activity["activity_id"], "Assessor One",
        datetime.date.today(), mtpd_hours=48, rto_hours=24,
    )
    gap = bia_engine.create_gap_analysis(
        conn, admin_id, bia["bia_id"],
        current_recovery_capability_hours=36, target_rto_hours=24,
        risk_summary="Current recovery capability exceeds target RTO.",
    )
    # DB-generated column: 36 - 24 = 12
    assert gap["gap_hours"] == 12

    fetched = bia_engine.get_gap_analysis(conn, gap["gap_id"])
    assert fetched["gap_hours"] == 12

    gap_closed = bia_engine.create_gap_analysis(
        conn, admin_id, bia["bia_id"],
        current_recovery_capability_hours=20, target_rto_hours=24,
        risk_summary="Recovery capability now meets target.",
    )
    assert gap_closed["gap_hours"] == -4  # negative = capability better than target


def test_detect_single_points_of_failure(conn, activity, org):
    admin_id = org["admin_user_id"]
    org_id = org["organization"]["organization_id"]

    spof_resource = bia_engine.create_resource(
        conn, admin_id, org_id, "technology", "Legacy Mainframe",
        is_single_point_of_failure=True,
    )
    normal_resource = bia_engine.create_resource(
        conn, admin_id, org_id, "personnel", "Backup Operator",
        is_single_point_of_failure=False,
    )
    bia_engine.link_activity_resource_dependency(conn, admin_id, activity["activity_id"], spof_resource["resource_id"])
    bia_engine.link_activity_resource_dependency(conn, admin_id, activity["activity_id"], normal_resource["resource_id"])

    spofs = bia_engine.detect_single_points_of_failure(conn, activity["activity_id"])
    assert len(spofs) == 1
    assert spofs[0]["resource_id"] == spof_resource["resource_id"]


# ---------------------------------------------------------------------------
# Recovery strategy selection — only one selected per bia_id
# ---------------------------------------------------------------------------


def test_only_one_recovery_strategy_selected_per_bia(conn, activity, org):
    admin_id = org["admin_user_id"]
    bia = bia_engine.create_bia_assessment(
        conn, admin_id, activity["activity_id"], "Assessor One",
        datetime.date.today(), mtpd_hours=48, rto_hours=24,
    )

    strat_a = bia_engine.create_recovery_strategy(
        conn, admin_id, bia["bia_id"], "hot_standby", "Hot Standby DC",
        "Fully mirrored hot standby datacenter.", is_selected_option=True,
    )
    assert strat_a["is_selected_option"] is True

    strat_b = bia_engine.create_recovery_strategy(
        conn, admin_id, bia["bia_id"], "warm_standby", "Warm Standby DC",
        "Partially provisioned warm standby datacenter.", is_selected_option=True,
    )
    assert strat_b["is_selected_option"] is True

    # Creating strat_b as selected must have un-marked strat_a.
    strategies = bia_engine.list_recovery_strategies_by_bia(conn, bia["bia_id"])
    selected = [s for s in strategies if s["is_selected_option"]]
    assert len(selected) == 1
    assert selected[0]["strategy_id"] == strat_b["strategy_id"]

    # Now explicitly re-select strat_a via select_recovery_strategy.
    bia_engine.select_recovery_strategy(conn, admin_id, bia["bia_id"], strat_a["strategy_id"])
    strategies_after = bia_engine.list_recovery_strategies_by_bia(conn, bia["bia_id"])
    selected_after = [s for s in strategies_after if s["is_selected_option"]]
    assert len(selected_after) == 1
    assert selected_after[0]["strategy_id"] == strat_a["strategy_id"]


# ---------------------------------------------------------------------------
# RBAC
# ---------------------------------------------------------------------------


def test_rbac_denies_insufficient_role(conn, activity, org):
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

    with pytest.raises(bia_engine.InsufficientRoleError) as excinfo:
        bia_engine.create_bia_assessment(
            conn, viewer_id, activity["activity_id"], "Should Fail",
            datetime.date.today(), mtpd_hours=48, rto_hours=24,
        )
    assert "viewer" in str(excinfo.value)


def test_rbac_allows_sufficient_role(conn, activity, org):
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

    bia = bia_engine.create_bia_assessment(
        conn, assessor_id, activity["activity_id"], "Should Succeed",
        datetime.date.today(), mtpd_hours=48, rto_hours=24,
    )
    assert bia["assessor_name"] == "Should Succeed"


def test_rbac_denies_unknown_user(conn, activity):
    fake_user_id = str(uuid.uuid4())
    with pytest.raises(bia_engine.InsufficientRoleError):
        bia_engine.create_bia_assessment(
            conn, fake_user_id, activity["activity_id"], "Should Fail",
            datetime.date.today(), mtpd_hours=48, rto_hours=24,
        )


def test_rbac_denies_inactive_user(conn, activity, org):
    org_id = org["organization"]["organization_id"]
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO application_users (organization_id, full_name, email, role, is_active)
            VALUES (%s, %s, %s, 'admin', FALSE) RETURNING user_id
            """,
            (org_id, "Deactivated Admin", f"deactivated-{uuid.uuid4()}@example.test"),
        )
        inactive_id = cur.fetchone()["user_id"]

    with pytest.raises(bia_engine.InsufficientRoleError) as excinfo:
        bia_engine.create_bia_assessment(
            conn, inactive_id, activity["activity_id"], "Should Fail",
            datetime.date.today(), mtpd_hours=48, rto_hours=24,
        )
    assert "deactivated" in str(excinfo.value).lower()


# ---------------------------------------------------------------------------
# Audit logging
# ---------------------------------------------------------------------------


def test_audit_log_written_on_create(conn, activity, org):
    admin_id = org["admin_user_id"]
    bia = bia_engine.create_bia_assessment(
        conn, admin_id, activity["activity_id"], "Assessor One",
        datetime.date.today(), mtpd_hours=48, rto_hours=24,
    )
    with conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM audit_logs WHERE entity_affected = 'bia_assessments' AND entity_id = %s",
            (str(bia["bia_id"]),),
        )
        rows = cur.fetchall()
    assert len(rows) == 1
    assert rows[0]["action"] == "CREATE"
    assert rows[0]["user_id"] == str(admin_id)


def test_audit_log_written_on_update(conn, activity, org):
    admin_id = org["admin_user_id"]
    bia = bia_engine.create_bia_assessment(
        conn, admin_id, activity["activity_id"], "Assessor One",
        datetime.date.today(), mtpd_hours=48, rto_hours=24,
    )
    bia_engine.update_bia_assessment(conn, admin_id, bia["bia_id"], rto_hours=12)
    with conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM audit_logs WHERE entity_affected = 'bia_assessments' AND entity_id = %s AND action = 'UPDATE'",
            (str(bia["bia_id"]),),
        )
        rows = cur.fetchall()
    assert len(rows) == 1
