"""Pytest suite for the Phase 5 Governance & Lifecycle module
(governance.py).

Covers, per the Phase 5 brief:
- create_sign_off_chain / submit_sign_off_decision / get_sign_off_status:
  the multi-tier (process owner -> top management) sign-off state
  machine, including the "block progression until prior tier signs off"
  rule, RBAC per required_role, one-shot decisions, and
  restart_sign_off_chain.
- compute_next_review_date: the calendar-month heuristic (including the
  day-of-month clamping edge case).
- document_review_schedule CRUD, mark_review_completed,
  list_upcoming_review_schedules / get_overdue_review_schedules.
- document_versions CRUD (create_document_version /
  record_maintenance_update), including duplicate version_label handling
  and the auto-incrementing minor version heuristic.
- RBAC denies/allows exactly like the existing pattern.
- Audit log rows written for sign_off_approvals, document_review_schedule,
  and document_versions writes.

Reuses the same fixtures (`conn`, `org`, `activity`) from tests/conftest.py
that the other test_*.py files use. Requires a running Postgres instance
with schema 001+002+003 applied. See TESTING.md.
"""

from __future__ import annotations

import datetime
import uuid

import pytest

from bcm_planner import bia_engine, governance


# ---------------------------------------------------------------------------
# Fixtures: a BIA assessment and an exercise debrief to hang governance
# rows off of (two different entity_type's backing tables).
# ---------------------------------------------------------------------------


@pytest.fixture()
def bia(conn, activity, org):
    admin_id = org["admin_user_id"]
    return bia_engine.create_bia_assessment(
        conn, admin_id, activity["activity_id"], "Assessor One",
        datetime.date.today(), mtpd_hours=48, rto_hours=24,
    )


@pytest.fixture()
def process_owner_user(conn, org):
    org_id = org["organization"]["organization_id"]
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO application_users (organization_id, full_name, email, role, is_active)
            VALUES (%s, %s, %s, 'approver_process_owner', TRUE) RETURNING user_id
            """,
            (org_id, "Process Owner", f"process-owner-{uuid.uuid4()}@example.test"),
        )
        return cur.fetchone()["user_id"]


@pytest.fixture()
def top_management_user(conn, org):
    org_id = org["organization"]["organization_id"]
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO application_users (organization_id, full_name, email, role, is_active)
            VALUES (%s, %s, %s, 'approver_top_management', TRUE) RETURNING user_id
            """,
            (org_id, "Top Management", f"top-mgmt-{uuid.uuid4()}@example.test"),
        )
        return cur.fetchone()["user_id"]


# ---------------------------------------------------------------------------
# create_sign_off_chain
# ---------------------------------------------------------------------------


def test_create_sign_off_chain_default_two_tiers(conn, org, bia):
    admin_id = org["admin_user_id"]
    chain = governance.create_sign_off_chain(conn, admin_id, "bia_assessment", bia["bia_id"])
    assert len(chain) == 2
    assert chain[0]["sequence_order"] == 1
    assert chain[0]["required_role"] == "approver_process_owner"
    assert chain[0]["decision"] == "pending"
    assert chain[1]["sequence_order"] == 2
    assert chain[1]["required_role"] == "approver_top_management"
    assert chain[1]["decision"] == "pending"


def test_create_sign_off_chain_custom_tiers(conn, org, bia):
    admin_id = org["admin_user_id"]
    tiers = [
        {"sequence_order": 1, "required_role": "approver_process_owner"},
        {"sequence_order": 2, "required_role": "approver_top_management"},
        {"sequence_order": 3, "required_role": "admin"},
    ]
    chain = governance.create_sign_off_chain(conn, admin_id, "bia_assessment", bia["bia_id"], tiers=tiers)
    assert len(chain) == 3
    assert chain[2]["required_role"] == "admin"


def test_create_sign_off_chain_rejects_unknown_entity_type(conn, org, bia):
    admin_id = org["admin_user_id"]
    with pytest.raises(governance.BCMPlannerError) as excinfo:
        governance.create_sign_off_chain(conn, admin_id, "not_a_real_entity_type", bia["bia_id"])
    assert "Unrecognized entity_type" in str(excinfo.value)


def test_create_sign_off_chain_rejects_unknown_entity_id(conn, org):
    admin_id = org["admin_user_id"]
    with pytest.raises(governance.NotFoundError):
        governance.create_sign_off_chain(conn, admin_id, "bia_assessment", str(uuid.uuid4()))


def test_create_sign_off_chain_duplicate_raises_clear_error(conn, org, bia):
    admin_id = org["admin_user_id"]
    governance.create_sign_off_chain(conn, admin_id, "bia_assessment", bia["bia_id"])
    with pytest.raises(governance.DuplicateSignOffChainError) as excinfo:
        governance.create_sign_off_chain(conn, admin_id, "bia_assessment", bia["bia_id"])
    assert "already exists" in str(excinfo.value)


def test_create_sign_off_chain_for_exercise_debrief_entity_type(conn, org, activity):
    """exercise_debrief is the schema/003_... addition -- confirm it works
    end to end as an approval_entity_enum value, not just at the DB level."""
    admin_id = org["admin_user_id"]
    from bcm_planner import exercise_debrief, exercise_planner

    org_id = org["organization"]["organization_id"]
    programme = exercise_planner.create_exercise_programme(
        conn, admin_id, org_id, "Governance Test Programme", 2026, "Objectives.",
    )
    exercise = exercise_planner.create_exercise(
        conn, admin_id, programme["programme_id"], "discussion_based",
        "Governance Test Exercise", datetime.date(2026, 1, 1), "Facilitator",
        "Description.",
    )
    debrief = exercise_debrief.create_exercise_debrief(
        conn, admin_id, exercise["exercise_id"], "Debrief summary for governance test.",
    )
    chain = governance.create_sign_off_chain(conn, admin_id, "exercise_debrief", debrief["debrief_id"])
    assert len(chain) == 2


# ---------------------------------------------------------------------------
# get_sign_off_status / get_current_pending_tier
# ---------------------------------------------------------------------------


def test_get_sign_off_status_not_started(conn, org, bia):
    status = governance.get_sign_off_status(conn, "bia_assessment", bia["bia_id"])
    assert status["overall_status"] == "not_started"
    assert status["tiers"] == []
    assert status["current_tier"] is None


def test_get_sign_off_status_in_progress_then_approved(conn, org, bia, process_owner_user, top_management_user):
    admin_id = org["admin_user_id"]
    governance.create_sign_off_chain(conn, admin_id, "bia_assessment", bia["bia_id"])

    status = governance.get_sign_off_status(conn, "bia_assessment", bia["bia_id"])
    assert status["overall_status"] == "in_progress"
    assert status["current_tier"]["sequence_order"] == 1

    governance.submit_sign_off_decision(
        conn, process_owner_user, "bia_assessment", bia["bia_id"], 1, "approved",
        comments="Looks correct.",
    )
    status_mid = governance.get_sign_off_status(conn, "bia_assessment", bia["bia_id"])
    assert status_mid["overall_status"] == "in_progress"
    assert status_mid["current_tier"]["sequence_order"] == 2

    governance.submit_sign_off_decision(
        conn, top_management_user, "bia_assessment", bia["bia_id"], 2, "approved",
    )
    status_final = governance.get_sign_off_status(conn, "bia_assessment", bia["bia_id"])
    assert status_final["overall_status"] == "approved"
    assert status_final["current_tier"] is None


def test_get_sign_off_status_rejected(conn, org, bia, process_owner_user):
    admin_id = org["admin_user_id"]
    governance.create_sign_off_chain(conn, admin_id, "bia_assessment", bia["bia_id"])
    governance.submit_sign_off_decision(
        conn, process_owner_user, "bia_assessment", bia["bia_id"], 1, "rejected",
        comments="MTPD looks wrong.",
    )
    status = governance.get_sign_off_status(conn, "bia_assessment", bia["bia_id"])
    assert status["overall_status"] == "rejected"


def test_get_sign_off_status_returned_for_revision(conn, org, bia, process_owner_user):
    admin_id = org["admin_user_id"]
    governance.create_sign_off_chain(conn, admin_id, "bia_assessment", bia["bia_id"])
    governance.submit_sign_off_decision(
        conn, process_owner_user, "bia_assessment", bia["bia_id"], 1, "returned_for_revision",
    )
    status = governance.get_sign_off_status(conn, "bia_assessment", bia["bia_id"])
    assert status["overall_status"] == "returned_for_revision"


# ---------------------------------------------------------------------------
# submit_sign_off_decision — sequencing / blocking rule
# ---------------------------------------------------------------------------


def test_submit_sign_off_decision_blocks_later_tier_until_earlier_approved(
    conn, org, bia, process_owner_user, top_management_user
):
    admin_id = org["admin_user_id"]
    governance.create_sign_off_chain(conn, admin_id, "bia_assessment", bia["bia_id"])

    with pytest.raises(governance.SignOffSequenceError) as excinfo:
        governance.submit_sign_off_decision(
            conn, top_management_user, "bia_assessment", bia["bia_id"], 2, "approved",
        )
    assert "tier 1" in str(excinfo.value)
    assert "approver_process_owner" in str(excinfo.value)


def test_submit_sign_off_decision_allows_tier_2_after_tier_1_approved(
    conn, org, bia, process_owner_user, top_management_user
):
    admin_id = org["admin_user_id"]
    governance.create_sign_off_chain(conn, admin_id, "bia_assessment", bia["bia_id"])
    governance.submit_sign_off_decision(
        conn, process_owner_user, "bia_assessment", bia["bia_id"], 1, "approved",
    )
    decided = governance.submit_sign_off_decision(
        conn, top_management_user, "bia_assessment", bia["bia_id"], 2, "approved",
    )
    assert decided["decision"] == "approved"
    assert str(decided["approver_user_id"]) == str(top_management_user)


def test_submit_sign_off_decision_rejects_invalid_decision_value(conn, org, bia, process_owner_user):
    admin_id = org["admin_user_id"]
    governance.create_sign_off_chain(conn, admin_id, "bia_assessment", bia["bia_id"])
    with pytest.raises(governance.BCMPlannerError):
        governance.submit_sign_off_decision(
            conn, process_owner_user, "bia_assessment", bia["bia_id"], 1, "not_a_real_decision",
        )


def test_submit_sign_off_decision_unknown_tier_raises_not_found(conn, org, bia, process_owner_user):
    admin_id = org["admin_user_id"]
    governance.create_sign_off_chain(conn, admin_id, "bia_assessment", bia["bia_id"])
    with pytest.raises(governance.NotFoundError):
        governance.submit_sign_off_decision(
            conn, process_owner_user, "bia_assessment", bia["bia_id"], 99, "approved",
        )


def test_submit_sign_off_decision_already_decided_raises(conn, org, bia, process_owner_user):
    admin_id = org["admin_user_id"]
    governance.create_sign_off_chain(conn, admin_id, "bia_assessment", bia["bia_id"])
    governance.submit_sign_off_decision(
        conn, process_owner_user, "bia_assessment", bia["bia_id"], 1, "approved",
    )
    with pytest.raises(governance.SignOffAlreadyDecidedError) as excinfo:
        governance.submit_sign_off_decision(
            conn, process_owner_user, "bia_assessment", bia["bia_id"], 1, "rejected",
        )
    assert "already decided" in str(excinfo.value)


def test_submit_sign_off_decision_wrong_role_denied(conn, org, bia, top_management_user):
    """Top management cannot decide tier 1 (reserved for process owner)."""
    admin_id = org["admin_user_id"]
    governance.create_sign_off_chain(conn, admin_id, "bia_assessment", bia["bia_id"])
    with pytest.raises(governance.InsufficientRoleError):
        governance.submit_sign_off_decision(
            conn, top_management_user, "bia_assessment", bia["bia_id"], 1, "approved",
        )


def test_submit_sign_off_decision_admin_can_decide_any_tier(conn, org, bia):
    admin_id = org["admin_user_id"]
    governance.create_sign_off_chain(conn, admin_id, "bia_assessment", bia["bia_id"])
    decided = governance.submit_sign_off_decision(
        conn, admin_id, "bia_assessment", bia["bia_id"], 1, "approved",
    )
    assert decided["decision"] == "approved"


# ---------------------------------------------------------------------------
# restart_sign_off_chain
# ---------------------------------------------------------------------------


def test_restart_sign_off_chain_resets_all_tiers(conn, org, bia, process_owner_user):
    admin_id = org["admin_user_id"]
    governance.create_sign_off_chain(conn, admin_id, "bia_assessment", bia["bia_id"])
    governance.submit_sign_off_decision(
        conn, process_owner_user, "bia_assessment", bia["bia_id"], 1, "rejected",
        comments="Needs rework.",
    )
    restarted = governance.restart_sign_off_chain(conn, admin_id, "bia_assessment", bia["bia_id"])
    assert all(t["decision"] == "pending" for t in restarted)
    assert all(t["decision_date"] is None for t in restarted)
    assert all(t["approver_user_id"] is None for t in restarted)

    status = governance.get_sign_off_status(conn, "bia_assessment", bia["bia_id"])
    assert status["overall_status"] == "in_progress"


def test_restart_sign_off_chain_unknown_entity_raises_not_found(conn, org):
    admin_id = org["admin_user_id"]
    with pytest.raises(governance.NotFoundError):
        governance.restart_sign_off_chain(conn, admin_id, "bia_assessment", str(uuid.uuid4()))


# ---------------------------------------------------------------------------
# compute_next_review_date
# ---------------------------------------------------------------------------


def test_compute_next_review_date_simple_annual():
    result = governance.compute_next_review_date(datetime.date(2026, 3, 15), 12)
    assert result == datetime.date(2027, 3, 15)


def test_compute_next_review_date_crosses_year_boundary():
    result = governance.compute_next_review_date(datetime.date(2026, 11, 1), 3)
    assert result == datetime.date(2027, 2, 1)


def test_compute_next_review_date_clamps_day_of_month():
    # 2026-01-31 + 1 month -> Feb has only 28 days in 2026 (not a leap year)
    result = governance.compute_next_review_date(datetime.date(2026, 1, 31), 1)
    assert result == datetime.date(2026, 2, 28)


def test_compute_next_review_date_leap_year():
    result = governance.compute_next_review_date(datetime.date(2027, 1, 31), 13)
    # 2027-01-31 + 13 months = 2028-02, and 2028 is a leap year -> 29 days
    assert result == datetime.date(2028, 2, 29)


def test_compute_next_review_date_rejects_non_positive_frequency():
    with pytest.raises(governance.BCMPlannerError):
        governance.compute_next_review_date(datetime.date(2026, 1, 1), 0)


# ---------------------------------------------------------------------------
# document_review_schedule CRUD
# ---------------------------------------------------------------------------


def test_create_review_schedule_periodic_auto_computes_next_date(conn, org, bia):
    admin_id = org["admin_user_id"]
    schedule = governance.create_review_schedule(
        conn, admin_id, "bia_assessment", bia["bia_id"],
        review_frequency_months=12, last_reviewed_date=datetime.date(2026, 1, 1),
    )
    assert schedule["next_review_date"] == datetime.date(2027, 1, 1)
    assert schedule["review_trigger_type"] == "periodic"


def test_create_review_schedule_event_driven_requires_explicit_date(conn, org, bia):
    admin_id = org["admin_user_id"]
    with pytest.raises(governance.BCMPlannerError) as excinfo:
        governance.create_review_schedule(
            conn, admin_id, "bia_assessment", bia["bia_id"],
            review_trigger_type="event_driven",
        )
    assert "next_review_date must be supplied explicitly" in str(excinfo.value)


def test_create_review_schedule_event_driven_with_explicit_date(conn, org, bia):
    admin_id = org["admin_user_id"]
    schedule = governance.create_review_schedule(
        conn, admin_id, "bia_assessment", bia["bia_id"],
        review_trigger_type="event_driven",
        next_review_date=datetime.date(2026, 6, 1),
        trigger_event_description="Major incident triggered an ad-hoc review.",
    )
    assert schedule["next_review_date"] == datetime.date(2026, 6, 1)
    assert schedule["review_trigger_type"] == "event_driven"


def test_create_review_schedule_duplicate_raises_clear_error(conn, org, bia):
    admin_id = org["admin_user_id"]
    governance.create_review_schedule(
        conn, admin_id, "bia_assessment", bia["bia_id"], next_review_date=datetime.date(2026, 6, 1),
    )
    with pytest.raises(governance.BCMPlannerError) as excinfo:
        governance.create_review_schedule(
            conn, admin_id, "bia_assessment", bia["bia_id"], next_review_date=datetime.date(2026, 7, 1),
        )
    assert "already exists" in str(excinfo.value)


def test_get_review_schedule_for_entity_returns_none_if_absent(conn, org, bia):
    result = governance.get_review_schedule_for_entity(conn, "bia_assessment", bia["bia_id"])
    assert result is None


def test_update_review_schedule_roundtrip(conn, org, bia):
    admin_id = org["admin_user_id"]
    schedule = governance.create_review_schedule(
        conn, admin_id, "bia_assessment", bia["bia_id"], next_review_date=datetime.date(2026, 6, 1),
    )
    updated = governance.update_review_schedule(
        conn, admin_id, schedule["schedule_id"], notes="Escalated priority.",
    )
    assert updated["notes"] == "Escalated priority."

    with pytest.raises(governance.NotFoundError):
        governance.get_review_schedule(conn, str(uuid.uuid4()))

    with pytest.raises(governance.BCMPlannerError):
        governance.update_review_schedule(conn, admin_id, schedule["schedule_id"])

    with pytest.raises(governance.BCMPlannerError):
        governance.update_review_schedule(conn, admin_id, schedule["schedule_id"], not_a_real_field="x")


def test_mark_review_completed_periodic_advances_date(conn, org, bia):
    admin_id = org["admin_user_id"]
    schedule = governance.create_review_schedule(
        conn, admin_id, "bia_assessment", bia["bia_id"],
        review_frequency_months=6, next_review_date=datetime.date(2026, 6, 1),
    )
    completed = governance.mark_review_completed(
        conn, admin_id, schedule["schedule_id"], reviewed_date=datetime.date(2026, 6, 5),
    )
    assert completed["last_reviewed_date"] == datetime.date(2026, 6, 5)
    assert completed["next_review_date"] == datetime.date(2026, 12, 5)


def test_mark_review_completed_event_driven_requires_explicit_next_date(conn, org, bia):
    admin_id = org["admin_user_id"]
    schedule = governance.create_review_schedule(
        conn, admin_id, "bia_assessment", bia["bia_id"],
        review_trigger_type="event_driven", next_review_date=datetime.date(2026, 6, 1),
    )
    with pytest.raises(governance.BCMPlannerError):
        governance.mark_review_completed(conn, admin_id, schedule["schedule_id"])

    completed = governance.mark_review_completed(
        conn, admin_id, schedule["schedule_id"],
        reviewed_date=datetime.date(2026, 6, 5),
        next_review_date=datetime.date(2027, 1, 1),
    )
    assert completed["next_review_date"] == datetime.date(2027, 1, 1)


def test_list_upcoming_review_schedules_and_overdue(conn, org, bia, activity):
    admin_id = org["admin_user_id"]
    org_id = org["organization"]["organization_id"]

    soon = datetime.date.today() + datetime.timedelta(days=30)
    far = datetime.date.today() + datetime.timedelta(days=365)
    overdue = datetime.date.today() - datetime.timedelta(days=10)

    # Three distinct entities so each can get its own review schedule row
    # (UNIQUE(entity_type, entity_id)).
    bia2 = bia_engine.create_bia_assessment(
        conn, admin_id, activity["activity_id"], "Assessor Two",
        datetime.date.today(), mtpd_hours=72, rto_hours=36,
    )
    strat = bia_engine.create_recovery_strategy(
        conn, admin_id, bia["bia_id"], "hot_standby", "Hot Standby DC", "Description.",
    )

    soon_schedule = governance.create_review_schedule(
        conn, admin_id, "bia_assessment", bia["bia_id"], next_review_date=soon,
    )
    far_schedule = governance.create_review_schedule(
        conn, admin_id, "bia_assessment", bia2["bia_id"], next_review_date=far,
    )
    overdue_schedule = governance.create_review_schedule(
        conn, admin_id, "recovery_strategy", strat["strategy_id"], next_review_date=overdue,
    )

    upcoming = governance.list_upcoming_review_schedules(conn, within_days=90)
    upcoming_ids = {s["schedule_id"] for s in upcoming}
    assert soon_schedule["schedule_id"] in upcoming_ids
    assert far_schedule["schedule_id"] not in upcoming_ids
    assert overdue_schedule["schedule_id"] not in upcoming_ids

    overdue_list = governance.get_overdue_review_schedules(conn)
    overdue_ids = {s["schedule_id"] for s in overdue_list}
    assert overdue_schedule["schedule_id"] in overdue_ids
    assert soon_schedule["schedule_id"] not in overdue_ids


# ---------------------------------------------------------------------------
# document_versions
# ---------------------------------------------------------------------------


def test_create_document_version_roundtrip(conn, org, bia):
    admin_id = org["admin_user_id"]
    version = governance.create_document_version(
        conn, admin_id, "bia_assessment", bia["bia_id"], "1.0",
        {"mtpd_hours": 48, "rto_hours": 24}, change_summary="Initial version.",
    )
    assert version["version_label"] == "1.0"
    assert version["snapshot_json"]["mtpd_hours"] == 48

    fetched = governance.get_document_version(conn, version["version_id"])
    assert fetched["change_summary"] == "Initial version."

    with pytest.raises(governance.NotFoundError):
        governance.get_document_version(conn, str(uuid.uuid4()))


def test_create_document_version_duplicate_label_raises(conn, org, bia):
    admin_id = org["admin_user_id"]
    governance.create_document_version(conn, admin_id, "bia_assessment", bia["bia_id"], "1.0", {})
    with pytest.raises(governance.DuplicateVersionLabelError):
        governance.create_document_version(conn, admin_id, "bia_assessment", bia["bia_id"], "1.0", {})


def test_list_document_versions_for_entity_newest_first(conn, org, bia):
    admin_id = org["admin_user_id"]
    v1 = governance.create_document_version(conn, admin_id, "bia_assessment", bia["bia_id"], "1.0", {})
    v2 = governance.create_document_version(conn, admin_id, "bia_assessment", bia["bia_id"], "1.1", {})

    versions = governance.list_document_versions_for_entity(conn, "bia_assessment", bia["bia_id"])
    assert [v["version_label"] for v in versions] == ["1.1", "1.0"]

    latest = governance.get_latest_document_version(conn, "bia_assessment", bia["bia_id"])
    assert latest["version_id"] == v2["version_id"]


def test_get_latest_document_version_none_if_never_versioned(conn, org, bia):
    result = governance.get_latest_document_version(conn, "bia_assessment", bia["bia_id"])
    assert result is None


def test_record_maintenance_update_auto_increments_version_label(conn, org, bia):
    admin_id = org["admin_user_id"]

    first = governance.record_maintenance_update(
        conn, admin_id, "bia_assessment", bia["bia_id"], "First maintenance pass.",
    )
    assert first["version_label"] == "1.0"

    second = governance.record_maintenance_update(
        conn, admin_id, "bia_assessment", bia["bia_id"], "Second maintenance pass.",
        snapshot_json={"note": "adjusted RPO"},
    )
    assert second["version_label"] == "1.1"
    assert second["snapshot_json"]["note"] == "adjusted RPO"

    third = governance.record_maintenance_update(
        conn, admin_id, "bia_assessment", bia["bia_id"], "Third maintenance pass.",
    )
    assert third["version_label"] == "1.2"


def test_record_maintenance_update_explicit_label_overrides_auto_increment(conn, org, bia):
    admin_id = org["admin_user_id"]
    governance.record_maintenance_update(conn, admin_id, "bia_assessment", bia["bia_id"], "First.")
    explicit = governance.record_maintenance_update(
        conn, admin_id, "bia_assessment", bia["bia_id"], "Jump to major version.",
        version_label="2.0",
    )
    assert explicit["version_label"] == "2.0"


# ---------------------------------------------------------------------------
# RBAC — same allow/deny pattern as Phase 1/2/3/4
# ---------------------------------------------------------------------------


def test_rbac_denies_insufficient_role_for_sign_off_chain_create(conn, org, bia):
    org_id = org["organization"]["organization_id"]
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO application_users (organization_id, full_name, email, role, is_active)
            VALUES (%s, %s, %s, 'viewer', TRUE) RETURNING user_id
            """,
            (org_id, "Viewer User Governance", f"viewer-gov-{uuid.uuid4()}@example.test"),
        )
        viewer_id = cur.fetchone()["user_id"]

    with pytest.raises(governance.InsufficientRoleError) as excinfo:
        governance.create_sign_off_chain(conn, viewer_id, "bia_assessment", bia["bia_id"])
    assert "viewer" in str(excinfo.value)


def test_rbac_denies_insufficient_role_for_review_schedule_create(conn, org, bia):
    org_id = org["organization"]["organization_id"]
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO application_users (organization_id, full_name, email, role, is_active)
            VALUES (%s, %s, %s, 'viewer', TRUE) RETURNING user_id
            """,
            (org_id, "Viewer User Review", f"viewer-review-{uuid.uuid4()}@example.test"),
        )
        viewer_id = cur.fetchone()["user_id"]

    with pytest.raises(governance.InsufficientRoleError):
        governance.create_review_schedule(
            conn, viewer_id, "bia_assessment", bia["bia_id"], next_review_date=datetime.date(2026, 6, 1),
        )


def test_rbac_denies_insufficient_role_for_document_version_create(conn, org, bia):
    org_id = org["organization"]["organization_id"]
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO application_users (organization_id, full_name, email, role, is_active)
            VALUES (%s, %s, %s, 'viewer', TRUE) RETURNING user_id
            """,
            (org_id, "Viewer User Version", f"viewer-version-{uuid.uuid4()}@example.test"),
        )
        viewer_id = cur.fetchone()["user_id"]

    with pytest.raises(governance.InsufficientRoleError):
        governance.create_document_version(conn, viewer_id, "bia_assessment", bia["bia_id"], "1.0", {})


def test_rbac_denies_unknown_user_for_sign_off_decision(conn, org, bia):
    admin_id = org["admin_user_id"]
    governance.create_sign_off_chain(conn, admin_id, "bia_assessment", bia["bia_id"])
    fake_user_id = str(uuid.uuid4())
    with pytest.raises(governance.InsufficientRoleError):
        governance.submit_sign_off_decision(
            conn, fake_user_id, "bia_assessment", bia["bia_id"], 1, "approved",
        )


# ---------------------------------------------------------------------------
# Audit logging for new writes
# ---------------------------------------------------------------------------


def test_audit_log_written_for_sign_off_chain_create(conn, org, bia):
    admin_id = org["admin_user_id"]
    governance.create_sign_off_chain(conn, admin_id, "bia_assessment", bia["bia_id"])
    with conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM audit_logs WHERE entity_affected = 'sign_off_approvals' AND entity_id = %s",
            (str(bia["bia_id"]),),
        )
        rows = cur.fetchall()
    assert len(rows) == 1
    assert rows[0]["action"] == "CREATE"


def test_audit_log_written_for_sign_off_decision(conn, org, bia, process_owner_user):
    admin_id = org["admin_user_id"]
    governance.create_sign_off_chain(conn, admin_id, "bia_assessment", bia["bia_id"])
    governance.submit_sign_off_decision(
        conn, process_owner_user, "bia_assessment", bia["bia_id"], 1, "approved",
    )
    with conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM audit_logs WHERE entity_affected = 'sign_off_approvals' AND action = 'APPROVE'",
        )
        rows = cur.fetchall()
    assert len(rows) == 1
    assert rows[0]["user_id"] == str(process_owner_user)


def test_audit_log_written_for_review_schedule_create(conn, org, bia):
    admin_id = org["admin_user_id"]
    schedule = governance.create_review_schedule(
        conn, admin_id, "bia_assessment", bia["bia_id"], next_review_date=datetime.date(2026, 6, 1),
    )
    with conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM audit_logs WHERE entity_affected = 'document_review_schedule' AND entity_id = %s",
            (str(schedule["schedule_id"]),),
        )
        rows = cur.fetchall()
    assert len(rows) == 1
    assert rows[0]["action"] == "CREATE"


def test_audit_log_written_for_document_version_create(conn, org, bia):
    admin_id = org["admin_user_id"]
    version = governance.create_document_version(
        conn, admin_id, "bia_assessment", bia["bia_id"], "1.0", {},
    )
    with conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM audit_logs WHERE entity_affected = 'document_versions' AND entity_id = %s",
            (str(version["version_id"]),),
        )
        rows = cur.fetchall()
    assert len(rows) == 1
    assert rows[0]["action"] == "CREATE"
