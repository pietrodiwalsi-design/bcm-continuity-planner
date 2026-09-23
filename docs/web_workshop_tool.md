# Web Workshop Tool

This document describes the **web workshop tool** — a second, separate
deliverable built alongside the FastMCP server (`src/bcm_planner/mcp_server.py`,
used locally via Claude Desktop). Both share the exact same underlying
business logic (`bia_engine.py`, `bcp_generator.py`, `crisis_management.py`)
from `src/bcm_planner/`; neither duplicates the other's rules. Nothing in
this document changes how the MCP server is run or deployed.

The web workshop tool is a server-rendered FastAPI + Jinja2 app
(`src/bcm_planner/web/`) intended for live, in-person or video-call
workshops: Peter walks a prospective client through a BIA, generates a
draft BCP and CMT plan on the spot, and hands over PDF exports at the end
of the session — no login system, no client-side app build step, just
HTML forms and server-rendered pages.

## 1. Tenant isolation model

Every workshop client is one row in `organizations` (the same table used
by the MCP server / Phase 0-5 schema — no new organizations concept was
introduced). There is **no user login system** for this tool. Instead:

- Creating a workshop session (`POST /session/create`) inserts one new
  `organizations` row, one bootstrap `application_users` row (role
  `admin`, synthetic email `workshop-admin-{organization_id}@bcm-workshop.local`
  — there is no admin-email form field, since no authenticated user
  exists yet to type one in), and one `web_workshop_sessions` row (new
  table, `schema/004_web_workshop_sessions.sql`) that maps a random,
  unguessable `session_token` to that `organization_id` and
  `admin_user_id`.
- The session token is a 32-byte `secrets.token_urlsafe()` value (see
  `src/bcm_planner/web/workshop.py::generate_session_token`) — not a
  sequential ID, not derived from the organization name. It is the
  *only* credential; whoever holds the link holds full read/write access
  to that one organization's data, and nothing else.
- Every single route under `/session/{session_token}/...` in `app.py`
  first resolves the token to an `organization_id` via
  `tenancy.resolve_session()`, then passes that `organization_id` into
  one of the `get_*_scoped()` helpers in `tenancy.py`
  (`get_activity_scoped`, `get_business_process_scoped`,
  `get_bia_assessment_scoped`, `get_recovery_strategy_scoped`,
  `get_bc_plan_scoped`, `get_cmt_role_scoped`,
  `get_escalation_trigger_scoped`) before touching any entity by its
  own ID. Each of these helpers fetches the row via the existing
  `bia_engine.py` / `bcp_generator.py` / `crisis_management.py` getters
  (no duplicated SQL) and then explicitly compares the row's resolved
  `organization_id` — walking the same join chain traced in schema
  001/002 (`activities` -> `business_processes` -> `business_units` ->
  `organizations` for BIA-side entities; a direct `organization_id`
  column for `bc_plans`, `cmt_roles`, and `escalation_triggers`) — against
  the session's `organization_id`. Any mismatch raises
  `tenancy.TenantMismatchError`, which `app.py`'s exception handler maps
  to a plain HTTP 404 (deliberately indistinguishable from "doesn't
  exist" — a cross-tenant probe learns nothing).
- This is enforced in code, not by database row-level security — a
  pragmatic choice for a single-process demo tool at this scope (see
  REQUIREMENTS.md's stated scope), but it means **every new route added
  to `app.py` must remember to scope its lookups through `tenancy.py`**.
  `tests/test_web_tenancy.py` unit-tests each `get_*_scoped` helper
  directly, and `tests/test_web_app.py::test_tenant_isolation_org_a_cannot_access_org_b_via_any_route`
  proves it end-to-end over real HTTP: organization A builds a full
  BIA/BCP/CMT dataset, and every read, write, and PDF-export attempt by
  organization B's session token against A's entity IDs is confirmed to
  return 404, with zero leakage into B's own listing pages.

## 2. Session / access-code flow

1. Peter (or the client, live in the workshop) opens the tool's root URL
   and fills in the "Start a new workshop" form (`GET /`, `POST
   /session/create`): organization name, BCMS scope description,
   optional industry and workshop label.
2. The confirmation page shows the one-time access link
   (`/session/{session_token}/`) — this is the *only* place the token is
   ever displayed in full; bookmark or copy it immediately.
3. From then on, every page of the workshop (dashboard, BIA forms, BCP
   generation/detail, CMT roles/triggers, all 3 PDF exports) is reached
   through URLs prefixed with that same `/session/{session_token}/...`
   path. There's also a small "I have a link" lookup form
   (`GET /session/goto?token=...`) for re-entering a session token
   manually if the bookmark was lost.
4. There is no token expiry, rotation, or revocation mechanism in this
   phase — the token itself, plus normal HTTPS transport, is the only
   protection. Treat it like a password: don't post it anywhere public.

## 3. Running locally

```bash
# 1. Local Postgres (same docker-compose.yml as the MCP server's tests):
docker compose up -d db

# 2. Install the `web` optional dependency group (kept separate from core
#    deps so an MCP-only install via `pip install -e .` doesn't pull in
#    FastAPI/WeasyPrint unnecessarily — see pyproject.toml):
pip install -e ".[web]"

# 3. Export the same env vars TESTING.md already documents for pytest,
#    plus PORT (see .env.example):
set -a; source .env; set +a

# 4. Run the migration runner once (idempotent; safe to re-run — see
#    scripts/run_migrations.py's docstring for why this exists instead
#    of relying solely on docker-entrypoint-initdb.d):
python scripts/run_migrations.py

# 5. Start the app:
python -m bcm_planner.web.app
# or: uvicorn bcm_planner.web.app:app --reload
```

Then open `http://localhost:8000/`.

## 4. Deploying on Render

The blueprint is `render.yaml` at the repo root: one Docker web service
(`bcm-continuity-planner-web`, built from the root `Dockerfile`) plus one
managed Postgres instance (`bcm-continuity-planner-db`), wired together
via Render's `fromDatabase` env-var injection (`DATABASE_URL`, which
`src/bcm_planner/db.py::get_dsn()` already prefers over the discrete
`POSTGRES_*` vars when set — no code changes needed for Render vs. local
docker-compose).

**Migrations on Render's managed Postgres:** local dev uses
`docker-entrypoint-initdb.d` (see `docker-compose.yml`) to run
`schema/001`-`004` once, automatically, the first time a *fresh* volume
is created. Render's managed Postgres has no equivalent init-script mount
point, and schema files aren't safe to re-run unconditionally on every
deploy (their `CREATE TABLE`/`CREATE TYPE` statements aren't idempotent
and would fail on the second deploy). Instead, the Docker image's `CMD`
runs `scripts/run_migrations.py` before starting uvicorn on every
container start — it tracks applied migrations in a `schema_migrations`
table (created in `schema/004_web_workshop_sessions.sql`) and skips
anything already applied, so it's safe to run on every single deploy,
including the very first one against a brand-new empty database.

**Deploy steps (manual, one-time — requires Peter's own Render account
and billing; no Render API credentials were available in the environment
that built this blueprint):**

1. Go to [dashboard.render.com](https://dashboard.render.com), sign in
   (or create an account), and connect your GitHub account if not
   already connected.
2. Click **New +** -> **Blueprint**.
3. Select the `bcm-continuity-planner` GitHub repository (branch
   `main`) and let Render detect `render.yaml` at the repo root.
4. Review the two resources Render shows (the `bcm-continuity-planner-web`
   Docker web service and the `bcm-continuity-planner-db` Postgres
   database) and click **Apply**. Render provisions the database first,
   then builds and deploys the Docker image, wiring `DATABASE_URL`
   automatically per the blueprint.
5. Once the first deploy finishes (a few minutes — the Docker build
   compiles WeasyPrint's dependencies), open the web service's `.onrender.com`
   URL. `/healthz` should return `{"status":"ok","db":true}`; `/` should
   show the "Start a new workshop" form. Every subsequent push to `main`
   auto-deploys (`autoDeploy: true` in `render.yaml`).

Expected Render cost at the `starter` plan tier for both the web service
and the database: roughly EUR 13-18/month combined, in line with what
was budgeted for this portfolio project.

## 5. Promoting a completed workshop into the read-only dashboard

If a completed workshop's data is ever worth showcasing in the existing
read-only GitHub Pages demo dashboard (see `dashboard/` and
`scripts/export_dashboard_data.py`), there is no automated pipeline
connecting the two — this is a deliberate manual, opt-in step, since a
live client workshop's real data should never be published without an
explicit decision to do so. To do it:

1. Point `scripts/export_dashboard_data.py` at the same Postgres instance
   the web workshop tool used (locally, or Render's managed Postgres
   connection string from the dashboard's **Connect** tab) and run it as
   documented in that script's own usage notes.
2. Review the exported JSON for anything client-confidential before
   committing it — the dashboard is public.
3. Commit the refreshed export under `dashboard/data/` as normal.

## 6. Scope notes

Per the brief for this deliverable, this is a live-demo/workshop tool,
not a full admin CRUD app: editing or deleting previously-created
records is intentionally minimal. What's fully supported end-to-end for
each of BIA / BCP / CMT is **create + view + PDF export**, which is what
`tests/test_web_app.py` exercises and asserts.
