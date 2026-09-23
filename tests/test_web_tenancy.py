"""Pytest suite for src/bcm_planner/web/tenancy.py — the tenant isolation
enforcement layer for the public web workshop tool.

Covers, per the web-workshop-tool brief's core requirement ("every single
query/route in the new web layer MUST filter by the current session's
organization_id — no cross-tenant data leakage"):
- Each `get_*_scoped` helper returns the entity when it genuinely belongs
  to the given organization_id.
- Each `get_*_scoped` helper raises `TenantMismatchError` (not a silent
  None / not a raw NotFoundError) when the entity exists but belongs to a
  *different* organization_id — the exact scenario a malicious or
  mistaken workshop client request would trigger.
- `resolve_session` raises `SessionNotFoundError` for an unknown token and
  resolves correctly for a valid one.

Reuses the same fixtures (`conn`, `org`, `activity`) from
tests/conftest.py used by every other Phase 1-5 test module — this test
module follows that exact same rollback-per-test isolation pattern (no
new fixture style introduced). A second, throwaway organization
("org_b") is created directly via SQL per test that needs a genuine
cross-tenant counterpart, mirroring the `org` fixture's own bootstrap
pattern in conftest.py.
"""

from __future__ import annotations

import datetime
import uuid

import pytest

from bcm_planner import bcp_generator, bia_engine
from bcm_planner.web import tenancy


@pytest.fixture()
def org_b(conn):
    """A second, throwaway organization + admin user — the "wrong tenant"
    counterpart used by every cross-tenant-mismatch test in this module.
    Mirrors conftest.py's `org` fixture bootstrap exactly.
    """
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO organizations (name, bcms_scope_description) VALUES (%s, %s) RETURNING *",
            (f"Test Org B {uuid.uuid4()}", "Pytest fixture organization B"),
        )
        org_row = cur.fetchone()
        cur.execute(
            """
            INSERT INTO application_users (organization_id, full_name, email, role, is_active)
            VALUES (%s, %s, %s, 'admin', TRUE) RETURNING *
            """,
            (org_row["organization_id"], "Test Admin B", f"admin-b-{uuid.uuid4()}@example.test"),
        )
        admin_row = cur.fetchone()
    return {"organization": org_row, "admin_user_id": admin_row["user_id"]}


# ---------------------------------------------------------------------------
# resolve_session
# ---------------------------------------------------------------------------


def test_resolve_session_unknown_token_raises(conn):
    with pytest.raises(tenancy.SessionNotFoundError):
        tenancy.resolve_session(conn, "this-token-does-not-exist")


def test_resolve_session_valid_token_resolves(conn, org):
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO web_workshop_sessions (session_token, organization_id, admin_user_id)
            VALUES (%s, %s, %s) RETURNING *
            """,
            ("test-token-" + str(uuid.uuid4()), org["organization"]["organization_id"], org["admin_user_id"]),
        )
        session_row = cur.fetchone()
    resolved = tenancy.resolve_session(conn, session_row["session_token"])
    assert str(resolved["organization_id"]) == str(org["organization"]["organization_id"])


# ---------------------------------------------------------------------------
# get_activity_scoped
# ---------------------------------------------------------------------------


def test_get_activity_scoped_correct_org_succeeds(conn, org, activity):
    row = tenancy.get_activity_scoped(conn, org["organization"]["organization_id"], activity["activity_id"])
    assert str(row["activity_id"]) == str(activity["activity_id"])


def test_get_activity_scoped_wrong_org_raises_tenant_mismatch(conn, org, activity, org_b):
    with pytest.raises(tenancy.TenantMismatchError):
        tenancy.get_activity_scoped(conn, org_b["organization"]["organization_id"], activity["activity_id"])


def test_get_activity_scoped_nonexistent_raises_not_found(conn, org):
    with pytest.raises(bia_engine.NotFoundError):
        tenancy.get_activity_scoped(conn, org["organization"]["organization_id"], uuid.uuid4())


# ---------------------------------------------------------------------------
# get_business_process_scoped
# ---------------------------------------------------------------------------


def test_get_business_process_scoped_wrong_org_raises(conn, org, activity, org_b):
    process_id = activity["process_id"]
    with pytest.raises(tenancy.TenantMismatchError):
        tenancy.get_business_process_scoped(conn, org_b["organization"]["organization_id"], process_id)
    # Control: correct org succeeds.
    row = tenancy.get_business_process_scoped(conn, org["organization"]["organization_id"], process_id)
    assert str(row["process_id"]) == str(process_id)


# ---------------------------------------------------------------------------
# get_bia_assessment_scoped / get_recovery_strategy_scoped
# ---------------------------------------------------------------------------


def test_get_bia_assessment_scoped_wrong_org_raises(conn, org, activity, org_b):
    admin_id = org["admin_user_id"]
    bia = bia_engine.create_bia_assessment(
        conn, admin_id, activity["activity_id"], "Assessor One",
        datetime.date.today(), mtpd_hours=48, rto_hours=24,
    )
    with pytest.raises(tenancy.TenantMismatchError):
        tenancy.get_bia_assessment_scoped(conn, org_b["organization"]["organization_id"], bia["bia_id"])
    # Control: correct org succeeds.
    row = tenancy.get_bia_assessment_scoped(conn, org["organization"]["organization_id"], bia["bia_id"])
    assert str(row["bia_id"]) == str(bia["bia_id"])


def test_get_recovery_strategy_scoped_wrong_org_raises(conn, org, activity, org_b):
    admin_id = org["admin_user_id"]
    bia = bia_engine.create_bia_assessment(
        conn, admin_id, activity["activity_id"], "Assessor One",
        datetime.date.today(), mtpd_hours=48, rto_hours=24,
    )
    strategy = bia_engine.create_recovery_strategy(
        conn, admin_id, bia["bia_id"], "hot_standby", "Hot Standby DC", "desc",
    )
    with pytest.raises(tenancy.TenantMismatchError):
        tenancy.get_recovery_strategy_scoped(conn, org_b["organization"]["organization_id"], strategy["strategy_id"])
    row = tenancy.get_recovery_strategy_scoped(conn, org["organization"]["organization_id"], strategy["strategy_id"])
    assert str(row["strategy_id"]) == str(strategy["strategy_id"])


# ---------------------------------------------------------------------------
# get_bc_plan_scoped
# ---------------------------------------------------------------------------


def test_get_bc_plan_scoped_wrong_org_raises(conn, org, activity, org_b):
    admin_id = org["admin_user_id"]
    org_id = org["organization"]["organization_id"]
    plan = bcp_generator.create_bc_plan(
        conn, admin_id, org_id, "operational", "Test Plan", "Owner", "Invoke when...",
    )
    with pytest.raises(tenancy.TenantMismatchError):
        tenancy.get_bc_plan_scoped(conn, org_b["organization"]["organization_id"], plan["plan_id"])
    row = tenancy.get_bc_plan_scoped(conn, org_id, plan["plan_id"])
    assert str(row["plan_id"]) == str(plan["plan_id"])


# ---------------------------------------------------------------------------
# get_cmt_role_scoped / get_escalation_trigger_scoped
# ---------------------------------------------------------------------------


def test_get_cmt_role_scoped_wrong_org_raises(conn, org, org_b):
    from bcm_planner import crisis_management

    admin_id = org["admin_user_id"]
    org_id = org["organization"]["organization_id"]
    role = crisis_management.create_cmt_role(
        conn, admin_id, org_id, "Crisis Lead", "Jane Doe", "+1-555-0000", "jane@example.test",
        "Overall leadership",
    )
    with pytest.raises(tenancy.TenantMismatchError):
        tenancy.get_cmt_role_scoped(conn, org_b["organization"]["organization_id"], role["role_id"])
    row = tenancy.get_cmt_role_scoped(conn, org_id, role["role_id"])
    assert str(row["role_id"]) == str(role["role_id"])


def test_get_escalation_trigger_scoped_wrong_org_raises(conn, org, org_b):
    from bcm_planner import crisis_management

    admin_id = org["admin_user_id"]
    org_id = org["organization"]["organization_id"]
    trigger = crisis_management.create_escalation_trigger(
        conn, admin_id, org_id, "major", "Outage > 4h", 15, "Notify CMT",
    )
    with pytest.raises(tenancy.TenantMismatchError):
        tenancy.get_escalation_trigger_scoped(conn, org_b["organization"]["organization_id"], trigger["trigger_id"])
    row = tenancy.get_escalation_trigger_scoped(conn, org_id, trigger["trigger_id"])
    assert str(row["trigger_id"]) == str(trigger["trigger_id"])
