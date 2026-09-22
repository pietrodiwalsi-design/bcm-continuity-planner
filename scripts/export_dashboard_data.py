#!/usr/bin/env python3
"""Exports current BIA engine data from Postgres into dashboard/data.json.

Demo-focused, single static JSON export — no live API for the dashboard
(per the Phase 1 brief). Run this after entering/updating BIA data, then
open dashboard/index.html (it fetches ./data.json relative to itself).

Usage:
    python3 scripts/export_dashboard_data.py [--output dashboard/data.json]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from bcm_planner import bcp_generator, bia_engine, db  # noqa: E402


def _json_default(value: Any) -> Any:
    """Fallback JSON serializer for UUID/date/Decimal types returned by psycopg."""
    import datetime
    import decimal
    import uuid

    if isinstance(value, (uuid.UUID,)):
        return str(value)
    if isinstance(value, (datetime.date, datetime.datetime)):
        return value.isoformat()
    if isinstance(value, decimal.Decimal):
        return float(value)
    raise TypeError(f"Object of type {type(value)} is not JSON serializable")


def export_data(conn) -> dict[str, Any]:
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM organizations ORDER BY name")
        organizations = cur.fetchall()

        cur.execute("SELECT * FROM business_units ORDER BY name")
        business_units = cur.fetchall()

        cur.execute("SELECT * FROM business_processes ORDER BY name")
        business_processes = cur.fetchall()

        cur.execute("SELECT * FROM activities ORDER BY name")
        activities = cur.fetchall()

        cur.execute("SELECT * FROM resources ORDER BY name")
        resources = cur.fetchall()

        cur.execute("SELECT * FROM activity_resource_dependencies")
        dependencies = cur.fetchall()

        cur.execute("SELECT * FROM bia_assessments ORDER BY assessment_date DESC")
        bia_assessments = cur.fetchall()

        cur.execute("SELECT * FROM gap_analyses")
        gap_analyses = cur.fetchall()

        cur.execute("SELECT * FROM recovery_strategies")
        recovery_strategies = cur.fetchall()

        cur.execute("SELECT * FROM bc_plans ORDER BY created_at DESC")
        bc_plans = cur.fetchall()

        cur.execute("SELECT * FROM bcp_action_steps ORDER BY plan_id, step_number")
        bcp_action_steps = cur.fetchall()

        cur.execute("SELECT * FROM bau_return_procedures ORDER BY plan_id, phase_number")
        bau_return_procedures = cur.fetchall()

    # Build activity trees per process for the dashboard's hierarchy view.
    process_trees = []
    for process in business_processes:
        tree = bia_engine.get_activity_tree(conn, process["process_id"])
        process_trees.append({"process": process, "activity_tree": tree})

    # Attach SPOF flags per activity for quick dashboard lookup.
    spof_by_activity = {}
    for activity in activities:
        spofs = bia_engine.detect_single_points_of_failure(conn, activity["activity_id"])
        if spofs:
            spof_by_activity[str(activity["activity_id"])] = spofs

    # Phase 2: build a per-plan summary (action step count, source BIA/
    # activity provenance if auto-generated) for the dashboard's BCP view.
    action_step_counts: dict[str, int] = {}
    for step in bcp_action_steps:
        key = str(step["plan_id"])
        action_step_counts[key] = action_step_counts.get(key, 0) + 1

    bau_phase_counts: dict[str, int] = {}
    for phase in bau_return_procedures:
        key = str(phase["plan_id"])
        bau_phase_counts[key] = bau_phase_counts.get(key, 0) + 1

    activity_by_id = {str(a["activity_id"]): a for a in activities}

    bc_plans_summary = []
    for plan in bc_plans:
        plan_id = str(plan["plan_id"])
        provenance = bcp_generator.get_bc_plan_provenance(conn, plan["plan_id"])
        source_activity_name = None
        if provenance:
            source_activity_id = provenance["snapshot_json"].get("source_activity_id")
            if source_activity_id and source_activity_id in activity_by_id:
                source_activity_name = activity_by_id[source_activity_id]["name"]
        bc_plans_summary.append({
            "plan": plan,
            "action_step_count": action_step_counts.get(plan_id, 0),
            "bau_phase_count": bau_phase_counts.get(plan_id, 0),
            "provenance": provenance,
            "source_activity_name": source_activity_name,
        })

    return {
        "generated_at": __import__("datetime").datetime.utcnow().isoformat() + "Z",
        "organizations": organizations,
        "business_units": business_units,
        "business_processes": business_processes,
        "activities": activities,
        "resources": resources,
        "activity_resource_dependencies": dependencies,
        "bia_assessments": bia_assessments,
        "gap_analyses": gap_analyses,
        "recovery_strategies": recovery_strategies,
        "process_activity_trees": process_trees,
        "single_points_of_failure_by_activity": spof_by_activity,
        "bc_plans": bc_plans,
        "bcp_action_steps": bcp_action_steps,
        "bau_return_procedures": bau_return_procedures,
        "bc_plans_summary": bc_plans_summary,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", default=str(Path(__file__).resolve().parent.parent / "dashboard" / "data.json"),
        help="Output path for the exported JSON (default: dashboard/data.json)",
    )
    args = parser.parse_args()

    with db.get_connection() as conn:
        data = export_data(conn)

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(data, indent=2, default=_json_default), encoding="utf-8")
    print(f"Wrote dashboard data to {output_path} "
          f"({len(data['bia_assessments'])} BIA assessments, {len(data['activities'])} activities, "
          f"{len(data['bc_plans'])} BC plans).")


if __name__ == "__main__":
    main()
