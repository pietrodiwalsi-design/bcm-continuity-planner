# Testing — BCM Continuity Planner

The Phase 1 BIA Engine test suite runs against a real local PostgreSQL
instance (schema-heavy business rules like `chk_rto_less_than_mtpd` and the
`gap_hours` generated column are tested against actual Postgres behavior,
not mocked).

## Prerequisites

- Docker + Docker Compose (for the local Postgres instance).
- Python 3.10+ with the project installed (`pip install -e .` or
  `pip install -r requirements.txt` plus `pip install -e .` for the package
  itself).

## Running the suite

```bash
# 1. Copy env defaults (only needed once)
cp .env.example .env

# 2. Start Postgres — schema 001 then 002 are auto-applied on first boot
#    via docker-entrypoint-initdb.d (only runs against a fresh volume)
docker compose up -d

# 3. Wait for the healthcheck to pass (usually a few seconds)
docker compose ps   # STATUS should show "healthy"

# 4. Install the package + test dependencies
pip install -e .
pip install pytest   # or: pip install -r requirements.txt

# 5. Export the env vars from .env into your shell, then run pytest
set -a; . ./.env; set +a
pytest tests/ -v
```

Expected result at the time of writing: **35 passed** (14 Phase 1 `test_bia_engine.py` + 21 Phase 2 `test_bcp_generator.py`).

## What the suite covers

- `test_rto_less_than_mtpd_*` — the RTO < MTPD business rule is enforced
  both by a fast Python pre-check and by the database's
  `chk_rto_less_than_mtpd` CHECK constraint (translated into a clear
  `RTOConstraintViolation`, never a raw psycopg traceback).
- `test_hierarchy_crud_roundtrip_with_parent_child_activities` — the full
  organization → unit → product/service → process → activity chain,
  including self-referencing `parent_activity_id` parent/child activities.
- `test_gap_analysis_gap_hours_computed_correctly` — confirms the DB
  `GENERATED ALWAYS AS STORED` `gap_hours` column computes correctly for
  both an open gap (positive hours) and a closed gap (negative hours).
- `test_detect_single_points_of_failure` — the SPOF cross-reference helper
  correctly flags only resources marked
  `resources.is_single_point_of_failure = true`.
- `test_only_one_recovery_strategy_selected_per_bia` — confirms the
  application-level "only one selected strategy per bia_id" rule (there is
  no DB constraint for this — it's enforced in `bia_engine.py`).
- `test_rbac_*` — the RBAC helper denies writes for unknown/inactive/
  insufficient-role users and allows them for a sufficient role
  (`admin` / `bia_assessor`).
- `test_audit_log_written_on_*` — every create/update through the BIA
  engine writes exactly one row to the append-only `audit_logs` table.

### Phase 2 — `tests/test_bcp_generator.py` (21 tests)

- `test_bc_plan_crud_roundtrip` / `test_bcp_action_step_crud_roundtrip` —
  full CRUD round-trip for `bc_plans` and `bcp_action_steps`.
- `test_bcp_action_step_step_number_*` — `step_number > 0` enforced by
  both a Python pre-check and the DB CHECK constraint backstop (same
  pattern as Phase 1's RTO<MTPD tests).
- `test_generate_bcp_draft_from_bia_*` — auto-population produces an
  `invocation_criteria` string referencing the BIA's actual MTPD/RTO/RPO
  values, an `alternate_facility_details` value only for facility-implying
  recovery strategy categories, action steps matching the selected
  strategy's category template plus one step per resource dependency, and
  a `document_versions` provenance row linking the plan back to its source
  BIA/activity/recovery strategy. Also covers the "no selected strategy"
  error case.
- `test_bau_return_procedure_crud_roundtrip` — full CRUD round-trip for
  `bau_return_procedures`.
- `test_generate_bau_return_phases_produces_standard_phase_set` — confirms
  the fixed 4-phase Return-to-BAU set (Verify primary resource restoration
  → Parallel run/validation → Cutover to primary → Post-incident review
  handoff) is generated in order.
- `test_rbac_*` — same allow/deny pattern as Phase 1, reusing
  `bia_engine.require_role`.
- `test_audit_log_written_for_*` — audit log rows written for `bc_plans`,
  `bcp_action_steps`, `bau_return_procedures`, and the two auto-generation
  entry points.

See `docs/bcp_generation_rules.md` for the exact rule-based mapping these
tests verify.

## Test isolation

Each test gets its own `psycopg` connection (see `tests/conftest.py`), and
every write happens inside that connection without an explicit commit
inside the fixtures/tests themselves except where a helper function commits
internally via `db.get_connection()`. The `conn` fixture rolls back at
teardown, so most tests clean up after themselves; a few helper calls that
use `bia_engine` functions directly against the shared `conn` fixture also
roll back cleanly since `bia_engine`'s functions do not call `conn.commit()`
themselves (that's left to the caller — the MCP server's `db.get_connection()`
context manager commits on clean exit; tests use a bare `conn` and roll
back instead).

## Tearing down

```bash
docker compose down          # stop and remove the container, keep the data volume
docker compose down -v       # also delete the Postgres data volume (fresh start)
```
