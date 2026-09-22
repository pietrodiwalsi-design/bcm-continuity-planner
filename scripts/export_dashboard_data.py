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

from bcm_planner import bia_engine, db  # noqa: E402


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
          f"({len(data['bia_assessments'])} BIA assessments, {len(data['activities'])} activities).")


if __name__ == "__main__":
    main()
