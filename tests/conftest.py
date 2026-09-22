"""Pytest fixtures for the BIA engine test suite.

Requires a running Postgres instance with schema 001+002 applied (see
TESTING.md — `docker-compose up -d` handles both automatically on a fresh
volume via docker-entrypoint-initdb.d).

Each test gets a fresh connection wrapped in a transaction that is rolled
back at the end of the test, so tests don't need to worry about cleaning up
rows they create, and tests don't interfere with each other.
"""

from __future__ import annotations

import uuid

import psycopg
import pytest

from bcm_planner import bia_engine, db


@pytest.fixture()
def conn():
    connection = psycopg.connect(db.get_dsn(), row_factory=psycopg.rows.dict_row)
    yield connection
    connection.rollback()
    connection.close()


@pytest.fixture()
def org(conn):
    """Creates a throwaway organization + an admin user for use as `user_id`
    in write calls, directly via SQL (bootstrapping — before any user exists,
    RBAC has nothing to check against).
    """
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO organizations (name, bcms_scope_description) VALUES (%s, %s) RETURNING *",
            (f"Test Org {uuid.uuid4()}", "Pytest fixture organization"),
        )
        org_row = cur.fetchone()
        cur.execute(
            """
            INSERT INTO application_users (organization_id, full_name, email, role, is_active)
            VALUES (%s, %s, %s, 'admin', TRUE) RETURNING *
            """,
            (org_row["organization_id"], "Test Admin", f"admin-{uuid.uuid4()}@example.test"),
        )
        admin_row = cur.fetchone()
    return {"organization": org_row, "admin_user_id": admin_row["user_id"]}


@pytest.fixture()
def activity(conn, org):
    """Creates a minimal unit -> process -> activity chain for BIA tests."""
    admin_id = org["admin_user_id"]
    unit = bia_engine.create_business_unit(conn, admin_id, org["organization"]["organization_id"], "Test Unit")
    process = bia_engine.create_business_process(conn, admin_id, unit["unit_id"], "Test Process", "Process Owner Name")
    act = bia_engine.create_activity(conn, admin_id, process["process_id"], "Test Activity", "Activity Owner Name")
    return act
