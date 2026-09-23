"""Workshop session creation for the public web workshop tool.

A "workshop session" (in the web-tool sense, not a DB transaction) is:
one `organizations` row + one bootstrap `application_users` row (role
`admin`, used as the `user_id` for every RBAC-gated write the web UI makes
on this workshop's behalf) + one `web_workshop_sessions` row mapping a
random access token to that organization.

Bootstrapping the admin `application_users` row directly via SQL (not
through `bia_engine.create_organization`, which itself calls
`require_role` against an *existing* `application_users` row) mirrors the
exact pattern already used by `tests/conftest.py`'s `org` fixture — there
is no user to authorize against until the very first one is created for a
brand-new organization, so this bootstrap step is necessarily outside the
normal RBAC-gated write path, same as the test suite's setup.
"""

from __future__ import annotations

from typing import Any, Optional

import psycopg

from bcm_planner.web.tenancy import generate_session_token

__all__ = ["create_workshop_session", "get_workshop_session_by_organization"]


def create_workshop_session(
    conn: psycopg.Connection,
    organization_name: str,
    bcms_scope_description: str,
    industry: Optional[str] = None,
    workshop_label: Optional[str] = None,
    admin_full_name: str = "Workshop Facilitator",
) -> dict[str, Any]:
    """Creates a new organization + bootstrap admin application_users row +
    web_workshop_sessions access-token row, all in the caller's existing
    transaction (commits happen at the `db.get_connection()` context
    manager boundary, same as every other write in this codebase).

    Returns {"organization": <organizations row>, "admin_user_id": <uuid>,
    "session_token": <str>, "session": <web_workshop_sessions row>}.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO organizations (name, industry, bcms_scope_description)
            VALUES (%s, %s, %s)
            RETURNING *
            """,
            (organization_name, industry, bcms_scope_description),
        )
        organization = cur.fetchone()

        # Bootstrap admin user for this workshop. Email is synthesized from
        # the organization_id to satisfy the UNIQUE(email) constraint
        # without requiring a real facilitator email address for a
        # throwaway/demo workshop identity.
        bootstrap_email = f"workshop-admin-{organization['organization_id']}@bcm-workshop.local"
        cur.execute(
            """
            INSERT INTO application_users (organization_id, full_name, email, role, is_active)
            VALUES (%s, %s, %s, 'admin', TRUE)
            RETURNING *
            """,
            (organization["organization_id"], admin_full_name, bootstrap_email),
        )
        admin_user = cur.fetchone()

        session_token = generate_session_token()
        cur.execute(
            """
            INSERT INTO web_workshop_sessions (session_token, organization_id, admin_user_id, workshop_label)
            VALUES (%s, %s, %s, %s)
            RETURNING *
            """,
            (session_token, organization["organization_id"], admin_user["user_id"], workshop_label),
        )
        session_row = cur.fetchone()

    return {
        "organization": organization,
        "admin_user_id": admin_user["user_id"],
        "session_token": session_token,
        "session": session_row,
    }


def get_workshop_session_by_organization(
    conn: psycopg.Connection, organization_id: str
) -> Optional[dict[str, Any]]:
    """Reverse lookup: given an organization_id, return its
    web_workshop_sessions row (if any) — used by the "workshop created"
    confirmation page to redisplay the access link.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM web_workshop_sessions WHERE organization_id = %s",
            (str(organization_id),),
        )
        return cur.fetchone()
