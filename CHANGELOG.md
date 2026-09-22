# Changelog

All notable changes to this project are documented in this file. Dates are
in `YYYY-MM-DD` format. This file is the chronological record referenced by
the Documentation Governance rules in `DEVELOPMENT_PLAN.md`.

## 2026-09-22 — Phase 0 (repo scaffold) + Phase 1 (BIA Engine) complete

**Phase 0 — Repo scaffold:**
- `docker-compose.yml`: local PostgreSQL 16 (image `postgres:16-alpine`,
  satisfies the "14+" requirement) with a persistent named volume, exposed
  on `localhost:5432`, credentials via `.env` (see `.env.example`).
  `schema/001_core_schema.sql` and `schema/002_rbac_approvals_review_additions.sql`
  are mounted into `docker-entrypoint-initdb.d/` and auto-applied in order
  on first startup against a fresh volume.
- Python package `src/bcm_planner/` with `__init__.py`, `db.py`
  (psycopg v3 connection handling via a context manager), `bia_engine.py`
  (Phase 1 core logic), `mcp_server.py` (FastMCP server entrypoint).
- `pyproject.toml` (primary) + `requirements.txt` (fallback) declaring
  `fastmcp`, `psycopg[binary]`, `pydantic`, `pytest`.
- `.gitignore` for Python + Docker + the generated `dashboard/data.json`.

**Phase 1 — BIA Engine (FastMCP tools, `bcm_planner.mcp_server`):**
- Scope/hierarchy CRUD: organizations, business_units, products_services,
  business_processes, activities (with `parent_activity_id` support),
  resources, and activity_resource_dependencies.
- Impact matrix configuration: impact_categories, impact_thresholds CRUD.
- MTPD/RTO/RPO/MBCO capture: `bia_assessments` create/update. The
  `RTO < MTPD` rule is enforced by the existing DB CHECK constraint
  (`chk_rto_less_than_mtpd`); the Python layer adds a fast pre-check and
  translates any DB-level violation into a clear `RTOConstraintViolation`
  instead of a raw psycopg traceback.
- Gap analysis: `gap_analyses` create/read. `gap_hours` is the DB
  `GENERATED ALWAYS AS STORED` column — read back via `RETURNING *`, never
  written directly. Added a single-point-of-failure detection helper that
  cross-references `resources.is_single_point_of_failure = true` against
  `activity_resource_dependencies` for a given activity.
- Recovery strategy selection: `recovery_strategies` CRUD, plus a
  `select_recovery_strategy` helper enforcing "only one selected option per
  `bia_id`" in application logic (no DB constraint exists for this).
- RBAC helper (`require_role`) that reads `application_users.role` and
  `is_active`, checked against a real allow-list (`app_role_enum`
  `admin`/`bia_assessor` for writes) before every write — not decorative.
- Append-only audit logging: every create/update writes one row to
  `audit_logs` (action, entity_affected, entity_id, changes_json, user_id).

**Tests (`tests/test_bia_engine.py`, 14 tests, all passing against a live
Postgres instance via docker-compose):**
- RTO < MTPD constraint enforcement (Python pre-check + DB backstop).
- Hierarchy CRUD round-trip including parent/child activities.
- Gap analysis `gap_hours` computation (open and closed gap cases).
- Single-point-of-failure detection helper.
- Recovery strategy "only one selected per bia_id" logic.
- RBAC allow/deny (unknown user, inactive user, insufficient role,
  sufficient role).
- Audit log rows written on create and on update.

See `TESTING.md` for exact run instructions.

**Dashboard (demo-facing, static export — no live API):**
- `scripts/export_dashboard_data.py`: queries Postgres and writes
  `dashboard/data.json` (organizations, hierarchy, resources, BIA
  assessments, gap analyses, recovery strategies, nested activity trees,
  and a single-point-of-failure map).
- `dashboard/index.html` + `style.css` + `app.js`: standalone single-page
  view of the activity hierarchy tree, a BIA assessments table
  (MTPD/RTO/RPO/MBCO + color-coded gap status: red = gap open, green = gap
  closed) with selected recovery strategy, and a single-points-of-failure
  list. Verified: `data.json` structure is correct (seeded demo data — 2
  activities, 2 BIA assessments, 1 SPOF, 1 open + 1 closed gap) and
  `index.html`/`app.js` parse without errors.

**Documentation governance:**
- This `CHANGELOG.md` created.
- `DEVELOPMENT_PLAN.md` "Phase Status" table updated: Phase 0 and Phase 1
  marked Complete; relevant "Next Steps" checklist items ticked.
- `README.md` updated with a "Getting Started" section.
- `TESTING.md` created.
- No changes made to `schema/001_core_schema.sql` or
  `schema/002_rbac_approvals_review_additions.sql` — Phase 1 required no
  schema changes beyond what was already reviewed in `schema/SCHEMA_REVIEW.md`.

**Known limitations / deviations, disclosed honestly:**
- `docker-compose.yml` uses `postgres:16-alpine` rather than pinning to
  `14-alpine`. This satisfies the "PostgreSQL 14+" requirement and was a
  pragmatic choice (16-alpine was already locally cached); no 14-specific
  behavior is relied upon anywhere in the schema or code.
- The RBAC helper is intentionally minimal (role-field check only, no
  session/token auth) per the "personal portfolio/demo tool" scope decision
  in `REQUIREMENTS.md` — this is explicitly not a production auth system.
- The dashboard is read-only and demo-focused; it does not write back to
  Postgres. This matches the Phase 1 brief ("keep this simple and
  demo-focused, not a production frontend").
