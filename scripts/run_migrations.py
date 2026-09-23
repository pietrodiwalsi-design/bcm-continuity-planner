#!/usr/bin/env python3
"""Idempotent schema migration runner for environments where
docker-entrypoint-initdb.d is not available — specifically Render managed
Postgres (see docs/web_workshop_tool.md "Deploying on Render").

Local docker-compose keeps using docker-entrypoint-initdb.d (see
docker-compose.yml) for a *fresh* volume, which only ever runs once
against an empty data directory. Render's managed Postgres has no
equivalent init-script mount point, and re-running raw `schema/*.sql`
files unconditionally on every deploy would fail on the second deploy
(tables/types already exist). This script instead:

1. Ensures a `schema_migrations` tracking table exists (added in
   schema/004_web_workshop_sessions.sql, but created defensively here too
   in case this script ever runs before 004 is applied).
2. Applies each `schema/NNN_*.sql` file, in numeric order, exactly once —
   skipping any version already recorded in `schema_migrations`.
3. Records each newly-applied version in `schema_migrations` in the same
   transaction as the migration itself (all-or-nothing per file).

Used as the Docker entrypoint's first step in production (see
`Dockerfile` CMD) before starting uvicorn, and safe to run repeatedly
(e.g. on every Render deploy) against a database that already has some
or all migrations applied — including a fresh local dev Postgres volume
that already got 001-004 via docker-entrypoint-initdb.d (this script will
find all of them already recorded... actually not automatically, since
initdb.d doesn't populate schema_migrations. See "Bootstrapping an
already-initialized database" below.)

Bootstrapping an already-initialized local dev database (one created via
docker-entrypoint-initdb.d before this script existed): running this
script against it will attempt to CREATE TYPE/CREATE TABLE for 001-003
again and fail on "already exists" errors for those files specifically.
For that one-time local transition, mark 001-003 as already applied
without executing them first:
    python3 scripts/run_migrations.py --mark-applied 001,002,003
On a brand-new database (a fresh Render Postgres instance, or a fresh
local volume created *after* this script exists and 004 is the first
mounted migration for docker-entrypoint-initdb.d — see note in
docker-compose.yml), just run it normally with no flags; every migration
applies in order from scratch.

Usage:
    python3 scripts/run_migrations.py
    python3 scripts/run_migrations.py --mark-applied 001,002,003
    python3 scripts/run_migrations.py --dsn postgresql://...
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from bcm_planner import db  # noqa: E402

SCHEMA_DIR = Path(__file__).resolve().parent.parent / "schema"

_ENSURE_TRACKING_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version VARCHAR(50) PRIMARY KEY,
    applied_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);
"""


def _discover_migration_files() -> list[Path]:
    """Returns schema/NNN_*.sql files sorted by their numeric prefix."""
    files = sorted(SCHEMA_DIR.glob("[0-9][0-9][0-9]_*.sql"), key=lambda p: p.name)
    return files


def _version_from_filename(path: Path) -> str:
    return path.name.split("_", 1)[0]


def run_migrations(dsn: str | None = None) -> list[str]:
    """Applies every not-yet-applied schema/*.sql migration, in order.
    Returns the list of version strings newly applied (empty if the
    database was already fully up to date).
    """
    applied: list[str] = []
    conn = psycopg.connect(dsn or db.get_dsn())
    try:
        with conn.cursor() as cur:
            cur.execute(_ENSURE_TRACKING_TABLE_SQL)
        conn.commit()

        with conn.cursor() as cur:
            cur.execute("SELECT version FROM schema_migrations")
            already_applied = {row[0] for row in cur.fetchall()}

        for path in _discover_migration_files():
            version = _version_from_filename(path)
            if version in already_applied:
                print(f"[migrations] {version}: already applied, skipping.")
                continue
            sql = path.read_text()
            print(f"[migrations] {version}: applying {path.name} ...")
            with conn.cursor() as cur:
                cur.execute(sql)
                cur.execute(
                    "INSERT INTO schema_migrations (version) VALUES (%s) ON CONFLICT (version) DO NOTHING",
                    (version,),
                )
            conn.commit()
            applied.append(version)
            print(f"[migrations] {version}: applied.")
    finally:
        conn.close()
    return applied


def mark_applied(versions: list[str], dsn: str | None = None) -> None:
    """Records the given version strings as already-applied without
    executing their SQL — for bootstrapping an already-initialized
    database (see module docstring "Bootstrapping" section).
    """
    conn = psycopg.connect(dsn or db.get_dsn())
    try:
        with conn.cursor() as cur:
            cur.execute(_ENSURE_TRACKING_TABLE_SQL)
            for version in versions:
                cur.execute(
                    "INSERT INTO schema_migrations (version) VALUES (%s) ON CONFLICT (version) DO NOTHING",
                    (version,),
                )
        conn.commit()
        print(f"[migrations] marked as already-applied (no SQL executed): {', '.join(versions)}")
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dsn", default=None, help="Postgres DSN override (default: bcm_planner.db.get_dsn()).")
    parser.add_argument(
        "--mark-applied",
        default=None,
        help="Comma-separated version prefixes to record as already applied without running their SQL.",
    )
    args = parser.parse_args()

    if args.mark_applied:
        mark_applied([v.strip() for v in args.mark_applied.split(",") if v.strip()], dsn=args.dsn)
        return

    newly_applied = run_migrations(dsn=args.dsn)
    if newly_applied:
        print(f"[migrations] done. Newly applied: {', '.join(newly_applied)}")
    else:
        print("[migrations] done. Database already up to date.")


if __name__ == "__main__":
    main()
