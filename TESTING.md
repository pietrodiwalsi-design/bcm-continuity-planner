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

Expected result at the time of writing: **138 passed** (14 Phase 1 `test_bia_engine.py` + 21 Phase 2 `test_bcp_generator.py` + 22 Phase 3 `test_crisis_management.py` + 34 Phase 4 `test_exercise_planner.py` + 47 Phase 5 `test_governance.py`).

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

### Phase 3 — `tests/test_crisis_management.py` (22 tests)

- `test_cmt_role_crud_roundtrip` — full CRUD round-trip for `cmt_roles`,
  including the "no fields"/"unknown field" and not-found error paths.
- `test_escalation_trigger_crud_roundtrip` — full CRUD round-trip for
  `escalation_triggers`; `test_escalation_trigger_invalid_severity_level_raises`
  confirms an invalid `severity_level` value is rejected by the DB's
  `severity_level_enum`.
- `test_get_escalation_path_for_severity_low_severity_only_first_response_roles`
  / `test_get_escalation_path_for_severity_high_severity_notifies_all_roles`
  — a dedicated `cmt_roster` fixture (IT/Security Head, Operations Duty
  Manager, Legal Counsel, Spokesperson) demonstrates the documented
  heuristic: `minor` severity notifies only the first two (first-response)
  roles, `catastrophic` severity notifies all four. A third test confirms
  a severity with no matching `escalation_triggers` row still returns a
  computed (possibly empty) `notified_roles` list rather than erroring.
- `test_stakeholder_contact_crud_roundtrip` — full CRUD round-trip for
  `stakeholder_contact_matrices`.
- `test_message_bank_crud_roundtrip` — full CRUD round-trip for
  `message_bank`, including recording an explicit post-hoc legal approval
  via `update_message_bank_entry(pre_approved_by_legal=True)`.
- `test_generate_holding_statement_draft_*` — placeholder-token presence
  for `power_outage` and `data_breach` scenario types, scenario-type
  string normalization (`"Data Breach"`/`"data-breach"`/`"data_breach"`
  all resolve to the same template), the generic fallback template for an
  unrecognized scenario type, and — the safety-critical case —
  confirmation that `pre_approved_by_legal` is forced `False` in the
  result even when the caller explicitly passes `True`.
- `test_rbac_*` — same allow/deny pattern as Phase 1/2, reusing
  `bia_engine.require_role`.
- `test_audit_log_written_for_*` — audit log rows written for `cmt_roles`,
  `escalation_triggers`, `stakeholder_contact_matrices`, and
  `message_bank`.

See `docs/crisis_communication_templates.md` for the exact holding
statement template set and escalation-path heuristic these tests verify.

### Phase 4 — `tests/test_exercise_planner.py` (34 tests)

- `test_exercise_programme_crud_roundtrip` / `test_exercise_crud_roundtrip`
  — full CRUD round-trip for `exercise_programmes` and `exercises`,
  including no-field/unknown-field update error cases and an
  unknown-programme-id create raising `NotFoundError`.
- `test_exercise_invalid_category_raises` — an invalid `category` value
  is rejected by the DB's `exercise_category_enum`.
- `test_get_disruption_scenario_template_*` — sensible content for
  `power_outage`, `cyberattack_ddos` (with normalized/mixed-case input),
  and `key_supplier_failure`; `test_get_disruption_scenario_template_unknown_type_raises_documented_error`
  confirms the deliberate "error, not fallback" behavior for an
  unrecognized scenario type (unlike Phase 3's holding statement
  generator).
- `test_scenario_inject_crud_roundtrip` — full CRUD round-trip for
  `scenario_injects`.
- `test_get_exercise_storyboard_*` — correct time ordering across
  out-of-insertion-order injects; empty storyboard for a new exercise;
  `StoryboardValidationError` for a duplicate `sequence_number` and for a
  `sequence_number` that doesn't track `time_offset_minutes` order.
- `test_generate_injects_from_scenario_template_*` — plausible 3–5-item,
  time-ordered inject sets for `power_outage` and `cyberattack_ddos`,
  cross-checked against `get_exercise_storyboard`; unknown-exercise and
  unknown-scenario-type error cases.
- `test_exercise_debrief_crud_roundtrip` — full CRUD round-trip for
  `exercise_debriefs`; `test_second_debrief_for_same_exercise_raises_clear_error`
  confirms a second debrief for the same `exercise_id` raises
  `DuplicateDebriefError` with a clear message, not a raw DB traceback.
- `test_capa_action_item_crud_roundtrip` — full CRUD round-trip for
  `capa_action_items`; unknown-debrief-id create raises `NotFoundError`.
- `test_get_overdue_capa_items_identifies_overdue_not_ontrack` /
  `test_get_open_capa_items_includes_not_yet_due_open_items` — confirm the
  overdue-vs-on-track-vs-completed distinction and the open-vs-verified
  distinction.
- `test_rbac_*` — same allow/deny pattern as Phase 1/2/3, covering all
  five new entity types' create paths.
- `test_audit_log_written_for_*` — audit log rows written for
  `exercise_programmes`, `exercises`, `scenario_injects`,
  `exercise_debriefs`, and `capa_action_items`.

See `docs/exercise_scenario_templates.md` for the exact disruption
scenario templates, inject templates, and storyboard validation rules
these tests verify.

### Phase 5 — `tests/test_governance.py` (47 tests)

- `test_create_sign_off_chain_*` — default 2-tier and custom-tier chain
  creation; unrecognized `entity_type` and unknown `entity_id` error
  cases; duplicate-chain creation raises a clear error;
  `test_create_sign_off_chain_for_exercise_debrief_entity_type` confirms
  the new `exercise_debrief` `approval_entity_enum` value (migration 003)
  works end to end, not just at the DB level.
- `test_get_sign_off_status_*` — `not_started`, `in_progress` →
  `approved` progression, `rejected`, and `returned_for_revision` cases.
- `test_submit_sign_off_decision_*` — sequential blocking
  (`SignOffSequenceError` naming the specific blocking tier), successful
  tier-2-after-tier-1 progression, invalid decision value rejected,
  unknown tier `NotFoundError`, already-decided
  `SignOffAlreadyDecidedError`, wrong-role denial
  (`InsufficientRoleError` — top management cannot decide the
  process-owner tier), and confirmation `admin` can decide any tier.
- `test_restart_sign_off_chain_*` — full reset verified (every tier back
  to `pending`/`NULL` approver/decision_date), unknown-entity
  `NotFoundError`.
- `test_compute_next_review_date_*` — simple annual addition,
  year-boundary crossing, day-of-month clamping (both a non-leap and a
  leap-year February), non-positive-frequency rejection.
- `test_create_review_schedule_*` / `test_update_review_schedule_*` /
  `test_mark_review_completed_*` — periodic auto-computed
  `next_review_date`, event-driven explicit-date requirement (and its
  error case), duplicate-schedule error, `get_review_schedule_for_entity`
  returning `None` for an absent schedule, update round-trip (plus
  no-field/unknown-field error cases), and `mark_review_completed` for
  both periodic (auto-advance) and event-driven
  (explicit-next-date-required) schedules.
- `test_list_upcoming_review_schedules_and_overdue` — a 3-entity fixture
  set (soon/far/overdue) confirms the upcoming (within 90 days) and
  overdue buckets each contain exactly the expected schedule.
- `test_create_document_version_*` / `test_list_document_versions_*` /
  `test_record_maintenance_update_*` — create/get round-trip,
  duplicate-label error, newest-first ordering plus
  `get_latest_document_version`, `None` for a never-versioned entity,
  and `record_maintenance_update`'s auto-incrementing minor version
  label (`"1.0"` → `"1.1"` → `"1.2"`) plus explicit-label override.
- `test_rbac_*` — same allow/deny pattern as Phase 1-4, covering sign-off
  chain creation, review schedule creation, document version creation,
  and an unknown-user sign-off decision attempt.
- `test_audit_log_written_for_*` — audit log rows written for
  `sign_off_approvals` (both chain creation and decision submission),
  `document_review_schedule`, and `document_versions`.

See `docs/governance_lifecycle.md` for the exact sign-off state machine
and review-cycle scheduler rules these tests verify.

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
