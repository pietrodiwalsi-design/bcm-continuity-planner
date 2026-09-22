"""Database connection handling for the BCM Continuity Planner.

Uses psycopg (v3) against a local PostgreSQL 14+ instance (see
docker-compose.yml). Connection parameters are read from environment
variables (see .env.example), with DATABASE_URL taking precedence over
the discrete POSTGRES_* variables when set.

Kept deliberately simple (single-user, local/demo tool scope per
REQUIREMENTS.md) — no connection pooling framework, just short-lived
connections via a context manager.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Iterator

import psycopg
from psycopg.rows import dict_row


def get_dsn() -> str:
    """Builds a Postgres DSN from environment variables.

    DATABASE_URL, if set, is used as-is. Otherwise the DSN is assembled
    from POSTGRES_USER / POSTGRES_PASSWORD / POSTGRES_HOST / POSTGRES_PORT
    / POSTGRES_DB, matching docker-compose.yml and .env.example defaults.
    """
    url = os.environ.get("DATABASE_URL")
    if url:
        return url

    user = os.environ.get("POSTGRES_USER", "bcm_admin")
    password = os.environ.get("POSTGRES_PASSWORD", "bcm_local_dev_password")
    host = os.environ.get("POSTGRES_HOST", "localhost")
    port = os.environ.get("POSTGRES_PORT", "5432")
    db = os.environ.get("POSTGRES_DB", "bcm_continuity_planner")
    return f"postgresql://{user}:{password}@{host}:{port}/{db}"


@contextmanager
def get_connection(dsn: str | None = None) -> Iterator[psycopg.Connection]:
    """Yields a psycopg connection (dict-row cursor factory) as a context manager.

    Commits on clean exit, rolls back on exception, always closes the
    connection. Callers should use `with get_connection() as conn:` and
    `conn.cursor()` for queries.
    """
    conn = psycopg.connect(dsn or get_dsn(), row_factory=dict_row)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def healthcheck() -> bool:
    """Returns True if a trivial query against the database succeeds."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()
        return True
    except Exception:
        return False
