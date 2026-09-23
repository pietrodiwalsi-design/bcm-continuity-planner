"""Pytest suite for the web workshop tool's FastAPI app
(src/bcm_planner/web/app.py) — HTTP-level integration tests using
FastAPI's TestClient.

Covers, per the web-workshop-tool brief's required minimum:
- Organization/session creation via the `/session/create` route.
- Full create+view+PDF-export flow for BIA, BCP, and CMT (the brief's
  "it's fine if editing/deleting is minimal as long as create+view+
  PDF-export works reliably for each of BIA/BCP/CMT" bar).
- An explicit, HTTP-level tenant isolation test proving organization A
  cannot fetch organization B's data via any route (extends
  tests/test_web_tenancy.py's unit-level coverage with a true end-to-end
  HTTP check through the actual FastAPI routes, not just the tenancy
  helper functions directly).
- Each of the 3 PDF exports (BIA / BCP / CMT) produces a valid,
  non-empty PDF (`%PDF` magic-number check on the response body).

Unlike tests/conftest.py's `conn` fixture (which rolls back after every
test — appropriate for calling bia_engine/bcp_generator functions
directly against an open transaction), routes in app.py each open their
own short-lived connection via `db.get_connection()` per request (same
pattern as mcp_server.py) and commit at that connection's `with` block
exit. So HTTP-level tests in this module create real, committed rows and
clean them up explicitly in a fixture teardown by deleting the
organizations created during the test (cascades removes every dependent
row — business_units/activities/bia_assessments/bc_plans/cmt_roles/etc.
— per the ON DELETE CASCADE FKs already in schema/001+002).

Requires a running Postgres instance with schema 001-004 applied (see
TESTING.md) and the `web` optional dependency group installed
(`pip install -e .[web]`).
"""

from __future__ import annotations

import re
from typing import Iterator

import psycopg
import pytest
from fastapi.testclient import TestClient

from bcm_planner import db
from bcm_planner.web.app import app

TOKEN_RE = re.compile(r"/session/([A-Za-z0-9_-]{20,})/")


@pytest.fixture()
def client() -> Iterator[TestClient]:
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def cleanup_organizations() -> Iterator[list[str]]:
    """Tests append organization_id strings they create to this list;
    the fixture deletes them (cascade) after the test, regardless of
    pass/fail, so HTTP-level tests don't leave permanent rows in the
    shared local dev Postgres instance.
    """
    created_org_ids: list[str] = []
    yield created_org_ids
    if created_org_ids:
        conn = psycopg.connect(db.get_dsn(), row_factory=psycopg.rows.dict_row)
        try:
            with conn.cursor() as cur:
                for org_id in created_org_ids:
                    cur.execute("DELETE FROM organizations WHERE organization_id = %s", (org_id,))
            conn.commit()
        finally:
            conn.close()


def _create_workshop(client: TestClient, name: str) -> dict:
    """Creates a workshop session via the real HTTP route and returns
    {"token": str, "organization_id": str}, extracted from the rendered
    confirmation page and a direct DB lookup (organization_id isn't
    embedded in the HTML, so it's fetched by session_token).
    """
    resp = client.post(
        "/session/create",
        data={
            "organization_name": name,
            "bcms_scope_description": "Pytest end-to-end scope description.",
            "industry": "Testing",
        },
    )
    assert resp.status_code == 200
    match = TOKEN_RE.search(resp.text)
    assert match, f"Could not find session token in response for {name!r}"
    token = match.group(1)

    conn = psycopg.connect(db.get_dsn(), row_factory=psycopg.rows.dict_row)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT organization_id FROM web_workshop_sessions WHERE session_token = %s",
                (token,),
            )
            row = cur.fetchone()
    finally:
        conn.close()
    assert row is not None
    return {"token": token, "organization_id": str(row["organization_id"])}


def _build_full_bia_bcp_cmt(client: TestClient, token: str) -> dict:
    """Drives one workshop session through the full BIA -> BCP -> CMT
    create flow via real HTTP POSTs, mirroring exactly the manual flow a
    facilitator would click through in a live session. Returns key IDs
    fetched back from the DB for use by assertions.
    """
    conn = psycopg.connect(db.get_dsn(), row_factory=psycopg.rows.dict_row)
    try:
        resp = client.post(f"/session/{token}/bia/unit", data={"name": "HQ Operations"})
        assert resp.status_code in (200, 303)

        with conn.cursor() as cur:
            cur.execute(
                "SELECT bu.unit_id FROM business_units bu "
                "JOIN web_workshop_sessions s ON s.organization_id = bu.organization_id "
                "WHERE s.session_token = %s",
                (token,),
            )
            unit_id = str(cur.fetchone()["unit_id"])

        resp = client.post(
            f"/session/{token}/bia/process",
            data={"unit_id": unit_id, "name": "Order Fulfillment", "process_owner": "Bob Smith"},
        )
        assert resp.status_code in (200, 303)

        with conn.cursor() as cur:
            cur.execute("SELECT process_id FROM business_processes WHERE unit_id = %s", (unit_id,))
            process_id = str(cur.fetchone()["process_id"])

        resp = client.post(
            f"/session/{token}/bia/activity",
            data={"process_id": process_id, "name": "Payment Processing", "activity_owner": "Alice Jones"},
        )
        assert resp.status_code in (200, 303)

        with conn.cursor() as cur:
            cur.execute("SELECT activity_id FROM activities WHERE process_id = %s", (process_id,))
            activity_id = str(cur.fetchone()["activity_id"])

        resp = client.post(
            f"/session/{token}/bia/assessment",
            data={
                "activity_id": activity_id, "assessor_name": "Assessor A",
                "mtpd_hours": 48, "rto_hours": 24, "rpo_hours": 4, "mbco_percentage": 80,
            },
        )
        assert resp.status_code in (200, 303)

        with conn.cursor() as cur:
            cur.execute("SELECT bia_id FROM bia_assessments WHERE activity_id = %s", (activity_id,))
            bia_id = str(cur.fetchone()["bia_id"])

        resp = client.post(
            f"/session/{token}/bia/gap",
            data={
                "bia_id": bia_id, "current_recovery_capability_hours": 30,
                "target_rto_hours": 24, "risk_summary": "Test risk summary",
            },
        )
        assert resp.status_code in (200, 303)

        resp = client.post(
            f"/session/{token}/bia/strategy",
            data={
                "bia_id": bia_id, "category": "hot_standby", "strategy_name": "Hot Standby DC",
                "description": "Mirrored DC", "is_selected_option": "1",
            },
        )
        assert resp.status_code in (200, 303)

        resp = client.post(
            f"/session/{token}/bcp/generate",
            data={"bia_id": bia_id},
        )
        assert resp.status_code in (200, 303)

        with conn.cursor() as cur:
            cur.execute(
                "SELECT bp.plan_id FROM bc_plans bp "
                "JOIN web_workshop_sessions s ON s.organization_id = bp.organization_id "
                "WHERE s.session_token = %s",
                (token,),
            )
            plan_id = str(cur.fetchone()["plan_id"])

        resp = client.post(
            f"/session/{token}/cmt/role",
            data={
                "role_name": "Crisis Management Team Leader", "primary_assignee_name": "Carol White",
                "primary_assignee_phone": "+1-555-1234", "primary_assignee_email": "carol@example.test",
                "key_responsibilities": "Overall crisis leadership",
            },
        )
        assert resp.status_code in (200, 303)

        resp = client.post(
            f"/session/{token}/cmt/trigger",
            data={
                "severity_level": "major", "incident_condition": "Outage exceeds 4h",
                "notification_timeframe_minutes": 15, "required_action": "Notify full CMT",
            },
        )
        assert resp.status_code in (200, 303)

        return {"activity_id": activity_id, "bia_id": bia_id, "plan_id": plan_id}
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Organization / session creation
# ---------------------------------------------------------------------------


def test_create_workshop_session_creates_organization(client, cleanup_organizations):
    result = _create_workshop(client, f"Pytest Org {id(cleanup_organizations)}")
    cleanup_organizations.append(result["organization_id"])
    assert result["token"]
    assert result["organization_id"]

    resp = client.get(f"/session/{result['token']}/")
    assert resp.status_code == 200


def test_session_goto_unknown_token_is_404(client):
    resp = client.get("/session/goto", params={"token": "definitely-not-a-real-token"})
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Full BIA -> BCP -> CMT create+view+export flow
# ---------------------------------------------------------------------------


def test_full_bia_bcp_cmt_flow_end_to_end(client, cleanup_organizations):
    session = _create_workshop(client, f"Pytest Flow Org {id(cleanup_organizations)}")
    cleanup_organizations.append(session["organization_id"])
    token = session["token"]

    ids = _build_full_bia_bcp_cmt(client, token)

    # View pages render successfully.
    assert client.get(f"/session/{token}/bia").status_code == 200
    assert client.get(f"/session/{token}/bcp").status_code == 200
    assert client.get(f"/session/{token}/bcp/{ids['plan_id']}").status_code == 200
    assert client.get(f"/session/{token}/cmt").status_code == 200

    # BIA page shows the created activity and gap/strategy data.
    bia_page = client.get(f"/session/{token}/bia")
    assert "Payment Processing" in bia_page.text
    assert "Hot Standby DC" in bia_page.text

    # BCP detail page shows generated action steps.
    bcp_detail = client.get(f"/session/{token}/bcp/{ids['plan_id']}")
    assert "Activate hot standby site" in bcp_detail.text

    # CMT page shows the created role and trigger.
    cmt_page = client.get(f"/session/{token}/cmt")
    assert "Carol White" in cmt_page.text
    assert "Outage exceeds 4h" in cmt_page.text


# ---------------------------------------------------------------------------
# PDF exports — valid, non-empty PDF for each of BIA / BCP / CMT
# ---------------------------------------------------------------------------


def test_bia_pdf_export_is_valid_nonempty_pdf(client, cleanup_organizations):
    session = _create_workshop(client, f"Pytest BIA PDF Org {id(cleanup_organizations)}")
    cleanup_organizations.append(session["organization_id"])
    token = session["token"]
    _build_full_bia_bcp_cmt(client, token)

    resp = client.get(f"/session/{token}/bia/export.pdf")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/pdf"
    assert len(resp.content) > 500
    assert resp.content[:5] == b"%PDF-"


def test_bcp_pdf_export_is_valid_nonempty_pdf(client, cleanup_organizations):
    session = _create_workshop(client, f"Pytest BCP PDF Org {id(cleanup_organizations)}")
    cleanup_organizations.append(session["organization_id"])
    token = session["token"]
    ids = _build_full_bia_bcp_cmt(client, token)

    resp = client.get(f"/session/{token}/bcp/{ids['plan_id']}/export.pdf")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/pdf"
    assert len(resp.content) > 500
    assert resp.content[:5] == b"%PDF-"
    # Content-Disposition must be a valid, latin-1-safe header even though
    # the plan title contains a non-ASCII em-dash (bcp_generator's naming
    # convention) — regression check for the UnicodeEncodeError found and
    # fixed during this build.
    assert "attachment" in resp.headers["content-disposition"]


def test_cmt_pdf_export_is_valid_nonempty_pdf(client, cleanup_organizations):
    session = _create_workshop(client, f"Pytest CMT PDF Org {id(cleanup_organizations)}")
    cleanup_organizations.append(session["organization_id"])
    token = session["token"]
    _build_full_bia_bcp_cmt(client, token)

    resp = client.get(f"/session/{token}/cmt/export.pdf")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/pdf"
    assert len(resp.content) > 500
    assert resp.content[:5] == b"%PDF-"


# ---------------------------------------------------------------------------
# Tenant isolation — the core requirement, proven at the HTTP layer
# ---------------------------------------------------------------------------


def test_tenant_isolation_org_a_cannot_access_org_b_via_any_route(client, cleanup_organizations):
    """Organization A builds a full BIA/BCP/CMT dataset. Organization B
    (a completely separate workshop session) attempts to read, write, and
    export every one of A's entities using A's real IDs but B's session
    token. Every single attempt must be rejected (HTTP 404), and B's own
    listing pages must never render any of A's data.
    """
    org_a = _create_workshop(client, f"Pytest TenantA {id(cleanup_organizations)}")
    cleanup_organizations.append(org_a["organization_id"])
    org_b = _create_workshop(client, f"Pytest TenantB {id(cleanup_organizations)}")
    cleanup_organizations.append(org_b["organization_id"])

    token_a, token_b = org_a["token"], org_b["token"]
    ids = _build_full_bia_bcp_cmt(client, token_a)

    # --- Read attempts: B's token against A's entity IDs -> 404 ---
    assert client.get(f"/session/{token_b}/bcp/{ids['plan_id']}").status_code == 404
    assert client.get(f"/session/{token_b}/bcp/{ids['plan_id']}/export.pdf").status_code == 404

    # --- Write attempts: B's token trying to mutate/attach to A's entities -> 404 ---
    resp = client.post(
        f"/session/{token_b}/bia/gap",
        data={
            "bia_id": ids["bia_id"], "current_recovery_capability_hours": 1,
            "target_rto_hours": 1, "risk_summary": "cross-tenant injection attempt",
        },
    )
    assert resp.status_code == 404

    resp = client.post(
        f"/session/{token_b}/bia/strategy",
        data={
            "bia_id": ids["bia_id"], "category": "manual_workaround",
            "strategy_name": "Injected", "description": "should not be allowed",
        },
    )
    assert resp.status_code == 404

    resp = client.post(f"/session/{token_b}/bcp/generate", data={"bia_id": ids["bia_id"]})
    assert resp.status_code == 404

    conn = psycopg.connect(db.get_dsn(), row_factory=psycopg.rows.dict_row)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT process_id FROM activities WHERE activity_id = %s", (ids["activity_id"],))
            process_id = str(cur.fetchone()["process_id"])
    finally:
        conn.close()

    resp = client.post(
        f"/session/{token_b}/bia/activity",
        data={"process_id": process_id, "name": "Injected Activity", "activity_owner": "Attacker"},
    )
    assert resp.status_code == 404

    # --- Listing pages for B must show zero of A's data (no leakage) ---
    bia_page_b = client.get(f"/session/{token_b}/bia").text
    assert "Payment Processing" not in bia_page_b

    bcp_page_b = client.get(f"/session/{token_b}/bcp").text
    assert "Payment Processing" not in bcp_page_b

    cmt_page_b = client.get(f"/session/{token_b}/cmt").text
    assert "Carol White" not in cmt_page_b

    # --- Control: A can still access its own data throughout ---
    assert client.get(f"/session/{token_a}/bcp/{ids['plan_id']}").status_code == 200
