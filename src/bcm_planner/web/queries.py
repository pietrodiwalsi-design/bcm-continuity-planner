"""Small, read-only listing queries needed by the web UI that the
existing business-logic modules don't already expose (e.g. "all business
units for an organization"). These are plain `SELECT`s with no
calculation/derivation logic of their own — every actual BIA/BCP/CMT
computation still lives in `bia_engine.py` / `bcp_generator.py` /
`crisis_management.py`, imported and reused unchanged. This module exists
only because those modules were built against the MCP-tool access
pattern (fetch by parent ID you already have), which doesn't always
match what a browsable UI needs (fetch everything under an organization
to render a page).

Every query here is scoped by organization_id via the same join chains
documented in `tenancy.py` — kept in sync deliberately (see
`tenancy.py`'s module docstring for the full FK chain reference).
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import psycopg

__all__ = [
    "list_business_units_by_organization",
    "list_business_processes_by_organization",
    "list_activities_by_organization",
    "list_bcp_action_steps_and_bau_for_org",
]


def list_business_units_by_organization(conn: psycopg.Connection, organization_id: UUID | str) -> list[dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM business_units WHERE organization_id = %s ORDER BY name",
            (str(organization_id),),
        )
        return cur.fetchall()


def list_business_processes_by_organization(conn: psycopg.Connection, organization_id: UUID | str) -> list[dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT bp.*
            FROM business_processes bp
            JOIN business_units bu ON bu.unit_id = bp.unit_id
            WHERE bu.organization_id = %s
            ORDER BY bp.name
            """,
            (str(organization_id),),
        )
        return cur.fetchall()


def list_activities_by_organization(conn: psycopg.Connection, organization_id: UUID | str) -> list[dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT a.*, bp.name AS process_name
            FROM activities a
            JOIN business_processes bp ON bp.process_id = a.process_id
            JOIN business_units bu ON bu.unit_id = bp.unit_id
            WHERE bu.organization_id = %s
            ORDER BY a.name
            """,
            (str(organization_id),),
        )
        return cur.fetchall()


def list_bcp_action_steps_and_bau_for_org(conn: psycopg.Connection, plan_id: UUID | str) -> dict[str, Any]:
    """Convenience combined fetch used by the BCP detail/export routes
    (action steps + BAU return phases for one plan). No org filter needed
    here directly — the caller has already resolved/verified `plan_id`
    via `tenancy.get_bc_plan_scoped` before calling this.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM bcp_action_steps WHERE plan_id = %s ORDER BY step_number",
            (str(plan_id),),
        )
        action_steps = cur.fetchall()
        cur.execute(
            "SELECT * FROM bau_return_procedures WHERE plan_id = %s ORDER BY phase_number",
            (str(plan_id),),
        )
        bau_phases = cur.fetchall()
    return {"action_steps": action_steps, "bau_phases": bau_phases}
