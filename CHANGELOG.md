# Changelog

All notable changes to this project are documented in this file. Dates are
in `YYYY-MM-DD` format. This file is the chronological record referenced by
the Documentation Governance rules in `DEVELOPMENT_PLAN.md`.

## 2026-09-23 — Phase 5 (Governance & Lifecycle) complete

**Phase 5 — Governance & Lifecycle (`src/bcm_planner/governance.py`,
FastMCP tools registered in `bcm_planner.mcp_server`), covering
FR14–FR15:**

- **Multi-tier sign-off workflow (FR14):**
  - `create_sign_off_chain(entity_type, entity_id, tiers=None)`: creates
    the ordered `sign_off_approvals` rows for one entity. Defaults to the
    standard 2-tier `approver_process_owner` → `approver_top_management`
    chain (`STANDARD_SIGN_OFF_TIERS`) per the brief's "process owner →
    top management" phrasing, or a caller-supplied custom `tiers` list.
    Duplicate-chain creation for the same entity raises a clear
    `DuplicateSignOffChainError` (pre-check + `SAVEPOINT`/`UniqueViolation`
    backstop, same pattern as Phase 4's `DuplicateDebriefError`).
  - `submit_sign_off_decision(entity_type, entity_id, sequence_order,
    decision, comments=None)`: the core state-machine rule — **a later
    tier cannot be decided until every earlier tier is `approved`**
    (`SignOffSequenceError`, naming the specific blocking tier); decisions
    are one-shot (`SignOffAlreadyDecidedError` on a repeat); RBAC gated on
    the **specific tier's `required_role`** (or `admin`) rather than the
    generic `WRITE_ROLES` allow-list — a deliberate, documented deviation
    from every other write in this codebase, since sign-off authority is
    narrower than general edit authority.
  - `get_sign_off_status(entity_type, entity_id)`: read-only
    `not_started`/`in_progress`/`approved`/`rejected`/
    `returned_for_revision` heuristic plus the current pending tier.
    `get_current_pending_tier`/`get_sign_off_approval`/
    `list_sign_off_approvals_for_entity` are the underlying read helpers.
  - `restart_sign_off_chain(entity_type, entity_id)`: explicit,
    `WRITE_ROLES`-gated reset of every tier back to `pending` — the only
    supported way to re-run a cycle after a rejection/return, once the
    document has been revised (no automatic re-trigger on document edit;
    left to the human, same as Phase 4's manual CAPA status transitions).

- **Version control & recurring review-cycle scheduler (FR15):**
  - `compute_next_review_date(base_date, review_frequency_months)`: a
    pure, stdlib-`calendar`-only month-arithmetic heuristic (no new
    dependency added), clamping the day-of-month for month-end/leap-year
    edge cases (`2026-01-31 + 1 month → 2026-02-28`;
    `2027-01-31 + 13 months → 2028-02-29`).
  - `document_review_schedule` CRUD: `create_review_schedule`,
    `get_review_schedule`, `get_review_schedule_for_entity` (returns
    `None`, not an error, if absent), `update_review_schedule`,
    `mark_review_completed`. Supports both `periodic` (`next_review_date`
    auto-computed, annual default per `review_frequency_months=12`) and
    `event_driven` (`next_review_date` must be supplied explicitly —
    raises a clear error otherwise) trigger types, matching
    REQUIREMENTS.md FR15's "annual/event-driven" wording exactly.
  - `list_upcoming_review_schedules(within_days=90)` and
    `get_overdue_review_schedules()`: read-only queries feeding the
    dashboard's new Governance section.
  - `document_versions` CRUD: `create_document_version`,
    `get_document_version`, `list_document_versions_for_entity`,
    `get_latest_document_version` — the same table Phase 2's
    `bcp_generator._record_bc_plan_provenance` already writes to, now
    with a general-purpose CRUD entry point spanning every governed
    entity type. Duplicate `version_label` for the same entity raises a
    clear `DuplicateVersionLabelError`. `created_at` is set via
    `clock_timestamp()` explicitly in the INSERT rather than relying on
    the column's `DEFAULT CURRENT_TIMESTAMP`, to keep "most recent
    version" ordering deterministic when multiple versions are created
    within the same transaction (`CURRENT_TIMESTAMP` is transaction-start
    time in Postgres, not wall-clock time).
  - `record_maintenance_update(entity_type, entity_id, change_summary,
    snapshot_json=None, version_label=None)`: FR15's maintenance-log
    convenience wrapper, auto-incrementing a `major.minor` version label
    (`"1.0"` → `"1.1"` → `"1.2"`, falling back to `"1.0"` if there is no
    prior version or the latest label doesn't parse as `major.minor`)
    unless an explicit `version_label` is given.

- **One new migration**,
  `schema/003_governance_test_report_entity_type.sql`: adds
  `exercise_debrief` to `approval_entity_enum` — migration 002's original
  4-value enum (`bia_assessment`/`bc_plan`/`recovery_strategy`/
  `crisis_management_plan`) had no value covering FR15's explicit "test
  reports" maintenance-log requirement. A standalone single-statement
  `ALTER TYPE ... ADD VALUE IF NOT EXISTS` file, kept separate from any
  other DDL per Postgres's restriction on that statement running inside a
  larger transaction alongside other statements that might use the new
  value. Every column Phase 5 needed on `sign_off_approvals`/
  `document_review_schedule`/`document_versions` was already fully
  defined in migration 002 — no other schema change was needed. Note
  appended to `schema/SCHEMA_REVIEW.md`. `docker-compose.yml` updated to
  mount migration 003 alongside 001/002 in `docker-entrypoint-initdb.d`
  order.

- **`crisis_management_plan` entity_type — documented scope gap:**
  accepted as a valid `entity_type` string (the enum value already
  existed, unused until now) but has **no existence check** performed
  against it — Phase 3 modeled crisis management as several tables
  (`cmt_roles`, `escalation_triggers`, etc.) rather than one
  `crisis_management_plan` row with a single PK, so there is no backing
  table to validate an `entity_id` against. Documented explicitly in
  `docs/governance_lifecycle.md` §1 rather than silently skipped.

- **RBAC + audit logging:** reuses `bia_engine.require_role`/
  `WRITE_ROLES`/`InsufficientRoleError` and `bia_engine.log_audit`
  unchanged — no second pattern introduced, with the one deliberate,
  documented exception noted above (tier-specific RBAC on
  `submit_sign_off_decision`). `mcp_server.py` gained 19 new FastMCP
  tools (98 total, version bumped `0.4.0` → `0.5.0`).

**Tests (`tests/test_governance.py`, 47 new tests, reusing the shared
`conn`/`org`/`activity` fixtures from `tests/conftest.py` plus new local
`bia`/`process_owner_user`/`top_management_user` fixtures) — full suite
now 138/138 passing (91 pre-existing + 47 new) against a live Postgres
instance via docker-compose:**

- `create_sign_off_chain`: default 2-tier and custom-tier creation;
  unrecognized `entity_type`/unknown `entity_id` error cases; duplicate-
  chain creation raises a clear error; end-to-end coverage of the new
  `exercise_debrief` entity type (confirming migration 003's enum value
  works, not just at the DB level).
- `get_sign_off_status`: `not_started`, `in_progress` → `approved`
  progression, `rejected`, and `returned_for_revision` cases.
- `submit_sign_off_decision`: sequential blocking (`SignOffSequenceError`
  naming the blocking tier), successful tier-2-after-tier-1 progression,
  invalid decision value rejected, unknown tier `NotFoundError`,
  already-decided `SignOffAlreadyDecidedError`, wrong-role denial
  (`InsufficientRoleError`), and `admin` able to decide any tier.
- `restart_sign_off_chain`: full reset verified (all tiers back to
  `pending`/`NULL` approver/decision_date), unknown-entity `NotFoundError`.
- `compute_next_review_date`: simple annual, year-boundary crossing,
  day-of-month clamping (non-leap and leap year), non-positive-frequency
  rejection.
- `document_review_schedule` CRUD: periodic auto-computed date,
  event-driven explicit-date requirement (and its error case),
  duplicate-schedule error, `get_review_schedule_for_entity` returning
  `None`, update round-trip (plus no-field/unknown-field error cases),
  `mark_review_completed` for both periodic (auto-advance) and
  event-driven (explicit-next-date-required) schedules,
  `list_upcoming_review_schedules`/`get_overdue_review_schedules` bucket
  correctness across three distinct entities.
- `document_versions`: create/get round-trip, duplicate-label error,
  newest-first ordering + `get_latest_document_version`, `None` for a
  never-versioned entity, `record_maintenance_update` auto-increment
  (`1.0` → `1.1` → `1.2`) and explicit-label override.
- `test_rbac_*` — same allow/deny pattern as Phase 1-4, covering
  sign-off chain creation, review schedule creation, document version
  creation, and an unknown-user sign-off decision.
- `test_audit_log_written_for_*` — audit log rows written for
  `sign_off_approvals` (chain create + decision submit),
  `document_review_schedule`, and `document_versions`.

See `TESTING.md` for exact run instructions (Phase 5 section to be added
alongside this entry).

**Dashboard (demo-facing, static export — extended, not rebuilt):**

- `scripts/export_dashboard_data.py`: now also exports
  `sign_off_approvals` (every tier row) and `sign_off_status_summary` (a
  compact per-entity overall-status + current-tier view, using the same
  heuristic as `governance.get_sign_off_status`), `review_schedules` plus
  pre-split `upcoming_review_schedules`/`overdue_review_schedules`
  buckets (90-day default window), and `document_versions` plus
  `latest_version_by_entity` for a compact "current version" lookup.
- `dashboard/index.html` + `app.js`: new **"Governance — Sign-Off
  Status"** table (entity type/id, tier count, overall status pill,
  current pending tier) and a new **"Governance — Document Review
  Schedule"** section with separate Overdue/Upcoming-within-90-days
  tables, each showing the current version label per entity.
- **Unlike Phase 3's exclusion of `message_bank`/
  `stakeholder_contact_matrices` content, sign-off comments and version
  `snapshot_json` ARE included** in this export — sign-off decisions and
  version history are the entire point of a governance dashboard view,
  not incidental sensitive contact/comms content.
- Verified end-to-end: ran `export_dashboard_data.py` against the live
  test/demo Postgres instance and confirmed `dashboard/data.json`
  contains all eight new top-level keys with the expected shape.

**Documentation governance:**

- This `CHANGELOG.md` entry.
- `docs/governance_lifecycle.md` created — the single source-of-truth
  for the Phase 5 sign-off state machine and review-cycle scheduler
  logic, same depth as `docs/exercise_scenario_templates.md` /
  `docs/crisis_communication_templates.md`.
- `schema/SCHEMA_REVIEW.md` updated with a note on migration 003.
- `DEVELOPMENT_PLAN.md` "Phase Status" table updated: Phase 5 marked
  **Complete** with a summary; Phase 5 section under "Phased Build Plan"
  expanded with a "Delivered" sub-section; "Next Steps" checklist items
  ticked for Phases 4 and 5.
- `README.md` updated: link to `docs/governance_lifecycle.md` added to
  the Documents list.

**Known limitations / deviations, disclosed honestly:**

- No notifications/reminders when a review is due or a sign-off tier is
  pending — out of scope per the brief's "lightweight state machine, no
  enterprise workflow engine" instruction; querying
  `get_overdue_review_schedules`/`get_current_pending_tier` is the
  intended pull-based usage pattern instead.
- No delegation/out-of-office handling for sign-off approvers — a tier's
  `required_role` must be held by whichever user submits the decision;
  there is no proxy/delegate mechanism.
- No auto-detection of "the underlying document changed" to
  auto-trigger a `restart_sign_off_chain` — left as an explicit human
  action, since this module has no visibility into what "changed" means
  for every entity type.
- `crisis_management_plan` entity_type is accepted by every governance
  function but has no existence check performed against it (no single
  backing table for it in the current schema) — documented in
  `docs/governance_lifecycle.md` §1, not silently skipped.

## 2026-09-22 — Phase 4 (Exercise & Test Planner) complete

**Phase 4 — Exercise & Test Planner (`src/bcm_planner/exercise_planner.py` +
`src/bcm_planner/scenario_injects.py` + `src/bcm_planner/exercise_debrief.py`,
FastMCP tools registered in `bcm_planner.mcp_server`), covering FR11–FR13:**

- **Modular Exercise & Test Planner (FR11, `exercise_planner.py`):**
  - `exercise_programmes` CRUD: `create_exercise_programme`,
    `get_exercise_programme`, `update_exercise_programme`,
    `list_exercise_programmes_by_organization` — title,
    `annual_schedule_year`, objectives, `approved_budget`, scoped to
    `organization_id`.
  - `exercises` CRUD: `create_exercise`, `get_exercise`, `update_exercise`,
    `list_exercises_by_programme` — `category` (existing
    `exercise_category_enum`: discussion_based/scenario_tabletop/
    simulation/live/functional_test), title, planned_date,
    lead_facilitator, scenario_description, status — linked to
    `programme_id`. `create_exercise` validates the programme exists
    first, raising `NotFoundError` rather than a raw FK violation.
  - `get_disruption_scenario_template(scenario_type)`: a rule-based, pure
    (no DB write) helper returning a suggested `scenario_description`,
    suggested exercise `category`, and a short list of suggested
    objectives for `power_outage`, `cyberattack_ddos`, `data_breach`,
    `public_transit_disruption` (scenario_type keys deliberately reused
    from Phase 3's `crisis_communications.HOLDING_STATEMENT_TEMPLATES`)
    and `key_supplier_failure` (a Phase-4-only addition). Scenario type
    strings are normalized the same way as Phase 3 (case/space/hyphen/
    slash-insensitive). Full template set documented in the new
    `docs/exercise_scenario_templates.md` (same pattern as
    `docs/bcp_generation_rules.md` / `docs/crisis_communication_templates.md`).
  - **Deliberate deviation from the Phase 3 pattern:** unlike
    `generate_holding_statement_draft`'s generic fallback for an unknown
    `scenario_type`, `get_disruption_scenario_template` **raises a clear
    `BCMPlannerError`** (listing all known scenario types) for an
    unrecognized value instead of silently returning generic content —
    reasoned in `docs/exercise_scenario_templates.md`: a facilitator
    planning a bespoke exercise scenario should get an explicit signal to
    author their own content, not a mismatched template that looks
    authoritative.

- **Scenario Injects & Timeline Storyboarding (FR12,
  `scenario_injects.py`):**
  - `scenario_injects` CRUD: `create_scenario_inject`,
    `get_scenario_inject`, `update_scenario_inject`,
    `list_scenario_injects_by_exercise` — `sequence_number`,
    `time_offset_minutes`, `inject_title`, `inject_content`,
    `delivery_method`, `expected_team_action`, linked to `exercise_id`.
  - `get_exercise_storyboard(exercise_id)`: returns all injects for an
    exercise ordered by `time_offset_minutes`, but first validates that
    `sequence_number` values are unique and strictly increase in lockstep
    with `time_offset_minutes` order — raising a new
    `StoryboardValidationError` with a specific, actionable message
    (naming the duplicate `sequence_number`, or which two injects are out
    of order) rather than silently returning a broken timeline. This is a
    facilitator-authoring-error guard, not a database constraint (no
    schema change), since the correct sequencing is a planning concern.
  - `generate_injects_from_scenario_template(exercise_id, scenario_type)`:
    rule-based/templated (not AI-generated prose) auto-generation of a
    starter 4–5 inject set per `scenario_type` (same keys as
    `get_disruption_scenario_template`), e.g. cyberattack_ddos: T+0
    "Initial detection alert" → T+15 "IT confirms ransomware encryption
    spreading" → T+45 "Media inquiry received" → T+90 "Regulator
    notification deadline approaches" → T+180 "Systems restored from
    backup, verification requested". Generated injects are already
    time-ordered by construction, so the resulting storyboard always
    passes `get_exercise_storyboard` validation. Validates the exercise
    exists first (`NotFoundError`) and writes one aggregate audit log
    entry in addition to the per-inject CREATE logs.

- **Debrief, Hot-Debrief & Action Tracking (FR13, `exercise_debrief.py`):**
  - `exercise_debriefs` CRUD: `create_exercise_debrief`,
    `get_exercise_debrief`, `get_exercise_debrief_by_exercise`,
    `update_exercise_debrief` — `hot_debrief_summary`,
    `strengths_observed`, `weaknesses_observed`,
    `opportunities_for_improvement`, `identified_threats_risks`,
    `overall_rating`. **One debrief per `exercise_id`, enforced twice**:
    a fast Python pre-check (`SELECT ... WHERE exercise_id = %s`) raising
    a new `DuplicateDebriefError` with a clear message, plus the existing
    DB `UNIQUE(exercise_id)` constraint as an authoritative backstop
    (translated via `SAVEPOINT`/`UniqueViolation` catch into the same
    clean error, never a raw psycopg traceback) — identical pattern to
    Phase 1's `RTOConstraintViolation` and Phase 2's `step_number > 0`
    enforcement.
  - `capa_action_items` CRUD: `create_capa_action_item`,
    `get_capa_action_item`, `update_capa_action_item`,
    `list_capa_action_items_by_debrief` — `gap_description`,
    `corrective_action_required`, `assigned_owner`, `due_date`, `status`,
    `completion_date`, linked to `debrief_id`.
  - `get_overdue_capa_items(organization_id)` /
    `get_open_capa_items(organization_id)`: since neither
    `capa_action_items` nor `exercises` carry a direct
    `organization_id` column, both helpers join
    `capa_action_items -> exercise_debriefs -> exercises ->
    exercise_programmes` to resolve organization scope. "Overdue" =
    `due_date < CURRENT_DATE` and `status` not case-insensitively
    `Completed`/`Verified`; "open" = same completion check, any due date.
    This is the FR13 follow-through demo feature — showing the tool
    tracks whether corrective actions actually get closed out, not just
    that exercises happened.

- **RBAC + audit logging:** all three new modules reuse
  `bia_engine.require_role` (same `WRITE_ROLES` allow-list,
  `InsufficientRoleError`) and `bia_engine.log_audit` unchanged — no
  second RBAC or logging pattern introduced. `mcp_server.py` gained new
  FastMCP tools covering every Phase 4 CRUD + helper function, version
  bumped to `0.4.0`.

- **No schema changes required.** `exercise_programmes`, `exercises`,
  `scenario_injects`, `exercise_debriefs`, and `capa_action_items` were
  already fully defined in `schema/001_core_schema.sql` (from the
  original schema proposal reviewed in `schema/SCHEMA_REVIEW.md`) —
  Phase 4 required zero migration work, only application-layer CRUD plus
  three rule-based helpers and two cross-table read-only aggregation
  helpers on top of existing tables.

**Tests (`tests/test_exercise_planner.py`, 34 new tests, reusing the
shared `conn`/`org` fixtures from `tests/conftest.py` plus new local
`programme`/`exercise`/`debrief` fixtures) — full suite now 91/91 passing
(57 pre-existing + 34 new) against a live Postgres instance via
docker-compose:**

- `exercise_programmes` and `exercises` CRUD round-trips, including
  no-field/unknown-field update error cases, not-found cases, and an
  unknown-programme-id create raising `NotFoundError`.
- `get_disruption_scenario_template`: sensible content for
  `power_outage`, `cyberattack_ddos` (with normalized/mixed-case input),
  and `key_supplier_failure`; the unknown-scenario-type case asserts the
  raised error names both the bad input and the list of known types (the
  documented "error, not fallback" behavior).
- `scenario_injects` CRUD round-trip.
- `get_exercise_storyboard`: correct time ordering across
  out-of-insertion-order injects; empty storyboard for a new exercise;
  `StoryboardValidationError` for a duplicate `sequence_number`; and
  `StoryboardValidationError` for a `sequence_number` that doesn't track
  `time_offset_minutes` order.
- `generate_injects_from_scenario_template`: plausible 3–5-item,
  time-ordered (starting at offset 0) sets for both `power_outage` and
  `cyberattack_ddos`, cross-checked against `get_exercise_storyboard`;
  unknown exercise_id and unknown scenario_type error cases.
- `exercise_debriefs` CRUD round-trip; a second `create_exercise_debrief`
  call for the same `exercise_id` raises `DuplicateDebriefError` with a
  clear "already has a debrief" message (not a raw DB traceback).
- `capa_action_items` CRUD round-trip; unknown-debrief-id create raises
  `NotFoundError`.
- `get_overdue_capa_items`: a 3-item fixture set (one genuinely overdue
  and open, one open but not yet due, one overdue-by-date but already
  Completed) confirms only the genuinely-overdue-and-open item is
  returned.
- `get_open_capa_items`: confirms a not-yet-due Open item is included and
  a Verified item is excluded.
- `test_rbac_*` — same allow/deny pattern as Phase 1/2/3, covering
  `exercise_programmes`, `exercises`, `scenario_injects`,
  `exercise_debriefs`, and `capa_action_items` create paths.
- `test_audit_log_written_for_*` — audit log rows written for all five
  new entity types: `exercise_programmes`, `exercises`,
  `scenario_injects`, `exercise_debriefs`, `capa_action_items`.

See `TESTING.md` for exact run instructions (Phase 4 section to be added
alongside this entry).

**Dashboard (demo-facing, static export — extended, not rebuilt):**

- `scripts/export_dashboard_data.py`: now also exports `exercises_summary`
  (each exercise joined with its programme, plus a computed
  `inject_count` and `debrief` status/`overall_rating` if one exists) and
  `overdue_capa_by_organization` (using the new
  `exercise_debrief.get_overdue_capa_items` helper directly, grouped per
  organization).
- `dashboard/index.html` + `app.js`: new **"Exercises & Tests"** section
  (title, category, planned_date, color-coded status pill, inject count,
  debrief status/rating) and a new **"Open / Overdue CAPA Action
  Items"** section (exercise title, gap_description, assigned_owner,
  due_date, status pill).
- **Deliberately did NOT include full `scenario_injects` content or
  `exercise_debriefs` narrative text in the dashboard** — kept to summary
  counts (inject count, debrief present/rating only) per the Phase 4
  brief, to avoid a cluttered demo view; same "summary, not full content"
  principle as Phase 3's decision to exclude `message_bank`/
  `stakeholder_contact_matrices` text.
- Verified end-to-end: seeded a demo organization → exercise programme →
  exercise (using `get_disruption_scenario_template("cyberattack_ddos")`
  for its category/description) → 5 generated injects → a debrief → one
  deliberately-overdue CAPA item, ran `export_dashboard_data.py`, and
  confirmed `dashboard/data.json` contains the expected
  `exercises_summary` entry (`inject_count: 5`, debrief present with
  rating) and `overdue_capa_by_organization` entry (1 overdue item);
  demo/seed data was then deleted so as not to leave test rows in the
  shipped repo state.

**Documentation governance:**

- This `CHANGELOG.md` entry.
- `docs/exercise_scenario_templates.md` created — the single
  source-of-truth for the Phase 4 rule-based templates (disruption
  scenario templates, inject templates, storyboard validation rules).
  Linked from `README.md`.
- `DEVELOPMENT_PLAN.md` "Phase Status" table updated: Phase 4 marked
  **Complete** with a summary; Phase 4 section under "Phased Build Plan"
  expanded with a "Delivered" sub-section.
- `README.md` updated: "Status" line, a new "Trying the Exercise & Test
  Planner (Phase 4)" subsection under Getting Started, and a link to
  `docs/exercise_scenario_templates.md` in the Documents list.
- No changes made to `schema/001_core_schema.sql` or
  `schema/002_rbac_approvals_review_additions.sql` — confirmed no new
  migration was needed (see `schema/SCHEMA_REVIEW.md`; no new note added
  since nothing changed there).

**Known limitations / deviations, disclosed honestly:**

- The storyboard-ordering rule (`sequence_number` must strictly increase
  in lockstep with `time_offset_minutes`) is a straightforward, fully
  documented invariant, not a configurable facilitation/scheduling engine
  — consistent with the "personal portfolio/demo tool, data model + 
  rule-based content generation + tracking only" scope decision in
  `REQUIREMENTS.md`.
- No live/real-time multi-user exercise-running UI, no
  calendar/scheduling integration, and no facilitator-facing broadcast
  tooling was built — explicitly out of scope per the Phase 4 brief.
- `get_overdue_capa_items`/`get_open_capa_items` treat `status` matching
  case-insensitively against a small fixed set (`Completed`, `Verified`)
  as "done" — not a configurable workflow/state machine; any other status
  string (including typos) is treated as still open, which is a
  deliberate "fail open" choice (an item should stay visible as a
  follow-up unless explicitly marked done) rather than a bug, but is
  called out here for transparency.
- No PDF/DOCX export of an "Exercise Report" combining debrief + CAPA
  items into a single document was built — out of scope for FR11–FR13
  specifically; data is accessible via the MCP CRUD tools and the
  (partial, summary-only) dashboard view, same pattern as prior phases.

## 2026-09-22 — Phase 3 (Crisis Management) complete

**Phase 3 — Crisis Management (`src/bcm_planner/crisis_management.py` +
`src/bcm_planner/crisis_communications.py`, FastMCP tools registered in
`bcm_planner.mcp_server`), covering FR9–FR10:**

- **Crisis Management Team (CMT) & Escalation Mapping (FR9,
  `crisis_management.py`):**
  - `cmt_roles` CRUD: `create_cmt_role`, `get_cmt_role`, `update_cmt_role`,
    `list_cmt_roles_by_organization` — role definitions (Crisis Management
    Team Leader, Legal Counsel, IT/Security Head, Spokesperson, etc.) with
    primary/alternate assignee contact details and key responsibilities,
    scoped to `organization_id`.
  - `escalation_triggers` CRUD: `create_escalation_trigger`,
    `get_escalation_trigger`, `update_escalation_trigger`,
    `list_escalation_triggers_by_organization` — severity-based triggers
    keyed on the existing `severity_level_enum` (minimal/minor/moderate/
    major/severe/catastrophic), with `notification_timeframe_minutes` and
    `required_action`.
  - `get_escalation_path_for_severity(organization_id, severity_level)`: a
    read-only helper returning the matching `escalation_triggers` row(s)
    plus the CMT roles that should be notified for that severity, using a
    simple, explicitly documented heuristic — major/severe/catastrophic
    notify **all** CMT roles for the organization; minimal/minor/moderate
    notify only roles whose `role_name`/`key_responsibilities` match a
    first-response keyword set (word-boundary regex match, not naive
    substring match — a naive match on short keywords like "it" would
    false-positive inside unrelated words such as "author**it**y"). See
    `HIGH_SEVERITY_ALL_ROLES_LEVELS` and `FIRST_RESPONSE_ROLE_KEYWORDS` in
    `crisis_management.py` for the exact rule.

- **Crisis Communication & Message Bank Builder (FR10,
  `crisis_communications.py`):**
  - `stakeholder_contact_matrices` CRUD: `create_stakeholder_contact`,
    `get_stakeholder_contact`, `update_stakeholder_contact`,
    `list_stakeholder_contacts_by_organization` — stakeholder group,
    contact person/entity, primary/backup channel, notification priority.
  - `message_bank` CRUD: `create_message_bank_entry`,
    `get_message_bank_entry`, `update_message_bank_entry`,
    `list_message_bank_entries_by_organization` — scenario type, target
    audience, holding statement template, `pre_approved_by_legal` flag,
    `dispatch_channels` array.
  - `generate_holding_statement_draft(scenario_type, target_audience)`: a
    rule-based, pure (no DB write) helper producing a DRAFT holding
    statement template with fill-in placeholder tokens
    (`{incident_summary}`, `{expected_resolution_time}`,
    `{contact_channel}`) for `power_outage`, `cyberattack_ddos`,
    `data_breach`, and `public_transit_disruption` scenario types (matched
    case/space/hyphen-insensitively), with a generic fallback template for
    any other `scenario_type`. Every template is prefixed
    `[DRAFT — NOT LEGALLY APPROVED]`. Full template set documented in the
    new `docs/crisis_communication_templates.md` (same pattern as
    `docs/bcp_generation_rules.md` for Phase 2).
  - **Safety-critical rule, enforced in code:**
    `generate_holding_statement_draft` always returns
    `pre_approved_by_legal=False` in its result, regardless of any
    caller-supplied value for that parameter — an auto-generated draft
    must never be represented as legally approved. Recording an actual
    legal sign-off is a separate, explicit, audited write via
    `update_message_bank_entry(..., pre_approved_by_legal=True)` on an
    existing row, never a side effect of drafting.

- **RBAC + audit logging:** both new modules reuse
  `bia_engine.require_role` (same `WRITE_ROLES` allow-list) and
  `bia_engine.log_audit` unchanged — no second RBAC or logging pattern was
  introduced.

- **No schema changes required.** `cmt_roles`, `escalation_triggers`,
  `stakeholder_contact_matrices`, and `message_bank` were already defined
  in `schema/001_core_schema.sql` (from the original schema proposal
  reviewed in `schema/SCHEMA_REVIEW.md`) — Phase 3 required zero migration
  work, only application-layer CRUD + the two rule-based helpers on top of
  existing tables.

**Tests (`tests/test_crisis_management.py`, 22 new tests, 57/57 passing
total against a live Postgres instance via docker-compose):**
- `cmt_roles` CRUD round-trip, including field-validation error paths.
- `escalation_triggers` CRUD round-trip, plus an invalid-`severity_level`
  DB-rejection case.
- `get_escalation_path_for_severity`: a dedicated `cmt_roster` fixture with
  a deliberately mixed roster (IT/Security Head, Operations Duty Manager,
  Legal Counsel, Spokesperson) demonstrates the heuristic difference —
  `minor` severity notifies only the first two roles; `catastrophic`
  severity notifies all four. Also covers the "trigger exists but no CMT
  roles match" and "no trigger row yet for this severity" cases.
- `stakeholder_contact_matrices` CRUD round-trip.
- `message_bank` CRUD round-trip, including recording an explicit
  post-hoc legal approval via `update_message_bank_entry`.
- `generate_holding_statement_draft`: placeholder-token presence for two
  distinct scenario types (`power_outage`, `data_breach`), scenario-type
  string normalization (`"Data Breach"` / `"data-breach"` /
  `"data_breach"` all resolve identically), the generic fallback template
  for an unrecognized scenario type, and — the safety-critical case — that
  `pre_approved_by_legal` is forced `False` in the output even when the
  caller explicitly passes `True`.
- `test_rbac_*` — same allow/deny pattern as Phase 1/2, reusing
  `bia_engine.require_role`.
- `test_audit_log_written_for_*` — audit log rows written for `cmt_roles`,
  `escalation_triggers`, `stakeholder_contact_matrices`, and `message_bank`.

**Dashboard (`scripts/export_dashboard_data.py`, `dashboard/`):**
- Extended with a **CMT Roster** table (role, primary contact
  name/phone/email, alternate contact, key responsibilities) and an
  **Escalation Summary** table (severity level with a color-coded pill —
  red for major/severe/catastrophic — notification timeframe, incident
  condition, required action), both grouped per-organization in the
  exported JSON (`cmt_roles_by_organization`,
  `escalation_summary_by_organization`) even though the current dashboard
  UI renders the flat lists directly (multi-org UI grouping left as a
  follow-up if Peter ever needs more than one demo organization visible at
  once).
- **Deliberately did NOT add `message_bank` or
  `stakeholder_contact_matrices` content to the dashboard export or UI** —
  those tables hold draft messaging text and named contact
  persons/entities that are not appropriate for a general-purpose,
  screenshot-friendly demo view. This was an explicit brief requirement,
  not an oversight; if a redacted view is wanted later (e.g. stakeholder
  group + channel only, no `contact_person_or_entity`), that is a Phase 3
  follow-up, not done here.

**Documentation governance:**
- This `CHANGELOG.md` entry.
- `DEVELOPMENT_PLAN.md` "Phase Status" table: Phase 3 marked **Complete**.
- `README.md`: linked `docs/crisis_communication_templates.md`, no other
  "Getting Started" changes needed (the existing MCP-tool-driven workflow
  description already covers how Phase 3 tools are used).
- `TESTING.md`: updated expected test count and added a Phase 3 section.
- No changes to `schema/001_core_schema.sql` or
  `schema/002_rbac_approvals_review_additions.sql` — confirmed no new
  migration was needed (see `schema/SCHEMA_REVIEW.md`, no new note added
  since nothing changed there).

**Known limitations / deviations, disclosed honestly:**
- The escalation-path heuristic (`FIRST_RESPONSE_ROLE_KEYWORDS`) is a
  simple, documented keyword-based rule, not a configurable
  notification-matrix engine — this was an explicit scope choice in the
  Phase 3 brief ("doesn't need to be sophisticated, just sensible and
  clearly documented").
- No live SMS/email/push dispatch integration (NFR14 is explicitly
  deferred per `REQUIREMENTS.md`'s scope decision) — `dispatch_channels`
  on `message_bank` and `generate_holding_statement_draft`'s output are
  data/text only, never sent anywhere.
- No PDF/DOCX export of the Crisis Management Plan (CMP) as a single
  document was built — out of scope for FR9/FR10 specifically; data is
  accessible via the MCP CRUD tools and the (partial) dashboard view, same
  pattern as Phase 1/2.
- The dashboard's per-organization grouping (`cmt_roles_by_organization`,
  `escalation_summary_by_organization`) is exported but the current
  `app.js` renders a flat (non-grouped-by-org) table — acceptable for a
  single-organization demo, called out here as a known simplification
  rather than silently left unmentioned.

## 2026-09-22 — Phase 2 (Plan Generators) complete

**Phase 2 — Plan Generators (`src/bcm_planner/bcp_generator.py`, FastMCP
tools registered in `bcm_planner.mcp_server`), covering FR7–FR8:**

- **BCP template builder & workflow generator (FR7):**
  - `bc_plans` CRUD: `create_bc_plan`, `get_bc_plan`, `update_bc_plan`,
    `list_bc_plans_by_organization`.
  - `bcp_action_steps` CRUD: `create_bcp_action_step`,
    `get_bcp_action_step`, `update_bcp_action_step`,
    `list_bcp_action_steps_by_plan`. `step_number > 0` is enforced by a
    fast Python pre-check (`InvalidStepNumberError`) and, as an
    authoritative backstop, by the existing DB `CHECK (step_number > 0)`
    constraint — translated into the same clear error, mirroring the
    Phase 1 `RTOConstraintViolation` pattern exactly.
  - `generate_bcp_draft_from_bia(bia_id, plan_tier='operational',
    plan_owner=None, recovery_strategy_id=None)`: rule-based (not
    AI-generated) auto-population of a **draft** `bc_plans` row plus a
    starter set of `bcp_action_steps`, derived from an existing Phase 1
    `bia_assessments` row, its activity's `activity_resource_dependencies`,
    and a selected (or explicitly given) `recovery_strategies` row:
    - `invocation_criteria` is built from the BIA's `mtpd_hours`,
      `rto_hours`, `rpo_hours` (if set), and `mbco_percentage`.
    - `alternate_facility_details` is copied from the strategy's
      description only when its `category` implies a physical/technical
      alternate facility (`hot_standby`, `warm_standby`, `cold_site`) —
      left `NULL` for `active_active`, `work_from_home`,
      `manual_workaround`.
    - Action steps: one fixed 3-step template per
      `strategy_category_enum` value (e.g. `hot_standby` → "Activate hot
      standby site" / "Redirect traffic/operations to standby resources"
      / "Validate standby site operational status"; `manual_workaround` →
      manual-process steps), **plus** one step per resource dependency
      ("Confirm availability of `{resource_type}` resource:
      `{resource_name}`"), each with a template `responsible_role` and
      `timeframe_offset_minutes`. Full mapping tables in
      `docs/bcp_generation_rules.md`.
    - Generated plans are always `version='0.1-draft'`,
      `status='Draft'` — auto-generation never marks a plan Approved.
  - **Provenance without schema changes:** rather than adding a column to
    `bc_plans`, `generate_bcp_draft_from_bia` writes one row to the
    existing `document_versions` table (schema/002, added for FR15) with a
    `snapshot_json` recording `source_bia_id`, `source_activity_id`,
    `source_recovery_strategy_id`, and `generation_method`. Read back via
    `get_bc_plan_provenance(plan_id)` — returns `None` for hand-authored
    plans. No new migration file was required; `schema/SCHEMA_REVIEW.md`
    was not touched since no schema change was made.

- **Return to BAU module (FR8), same module:**
  - `bau_return_procedures` CRUD: `create_bau_return_procedure`,
    `get_bau_return_procedure`, `update_bau_return_procedure`,
    `list_bau_return_procedures_by_plan`.
  - `generate_bau_return_phases(plan_id)`: rule-based auto-generation of
    the standard 4-phase Return-to-BAU set for an existing `bc_plan`:
    "Verify primary resource restoration" → "Parallel run / validation" →
    "Cutover to primary" → "Post-incident review handoff", each with a
    fixed `validation_criteria` and `action_steps` template. Not derived
    from BIA parameters (the BAU return sequence is generic across
    recovery strategies) — documented in `docs/bcp_generation_rules.md`.

- **RBAC + audit logging:** every write in this phase reuses
  `bia_engine.require_role` (same `WRITE_ROLES` allow-list, same
  `InsufficientRoleError`) and `bia_engine.log_audit` (same append-only
  `audit_logs` pattern) — no second RBAC or audit mechanism was
  introduced. `mcp_server.py` gained 21 new FastMCP tools (35 total,
  version bumped to `0.2.0`), following the exact same
  connection-per-call / `db.get_connection()` / error-translation pattern
  as the Phase 1 tools.

**Tests (`tests/test_bcp_generator.py`, 21 new tests, reusing the shared
`conn`/`org`/`activity` fixtures from `tests/conftest.py` plus one new
local `bia_with_strategy` fixture) — full suite now 35/35 passing against
a live Postgres instance via docker-compose:**

- `bc_plans` CRUD round-trip (create/get/update/list, unknown-field and
  no-field error cases, not-found case).
- `bcp_action_steps` CRUD round-trip, `step_number > 0` Python pre-check
  and DB CHECK constraint backstop (both zero/negative and DB-level
  paths).
- `generate_bcp_draft_from_bia`: invocation criteria references the exact
  MTPD/RTO/RPO values; `alternate_facility_details` populated for
  `hot_standby` and left `NULL` for `manual_workaround`; at least one
  generated action step matches the selected strategy category's
  template; at least one step derived from the resource dependency;
  sequential step numbering; provenance `document_versions` row correctly
  references source BIA/activity/strategy; "no selected recovery
  strategy" raises `NotFoundError`.
- `bau_return_procedures` CRUD round-trip.
- `generate_bau_return_phases` produces exactly the 4 expected phases in
  order; unknown `plan_id` raises `NotFoundError`.
- RBAC allow/deny (viewer denied, `bia_assessor` allowed, unknown user
  denied) for both direct CRUD and the auto-generation entry point.
- Audit log rows written for `bc_plans`, `bcp_action_steps`,
  `bau_return_procedures` creates, and both auto-generation entry points
  (the latter write both the underlying per-row CREATE logs and one
  summary log for the generation call itself).

See `TESTING.md` for exact run instructions and the full test breakdown.

**Dashboard (demo-facing, static export — extended, not rebuilt):**

- `scripts/export_dashboard_data.py`: now also exports `bc_plans`,
  `bcp_action_steps`, `bau_return_procedures`, and a computed
  `bc_plans_summary` (per-plan action step count, BAU phase count,
  auto-generation provenance with source activity name resolved).
- `dashboard/index.html` + `app.js`: new "Business Continuity Plans"
  section/table showing plan title, tier, version, status (color-coded
  pill), action step count, BAU return phase count, and a link back to
  the source BIA/activity for auto-generated plans ("Hand-authored" pill
  otherwise).
- Verified end-to-end: seeded one activity → BIA → selected `hot_standby`
  recovery strategy → `generate_bcp_draft_from_bia` →
  `generate_bau_return_phases`, then ran `export_dashboard_data.py` and
  confirmed `dashboard/data.json` contains the expected `bc_plans_summary`
  entry (4 action steps, 4 BAU phases, provenance linking back to the
  source BIA) and that `dashboard/app.js` parses without syntax errors
  (`node --check`).

**Documentation governance:**

- `docs/bcp_generation_rules.md` created — the single source of truth
  (human-readable) for the rule-based mapping tables implemented in
  `bcp_generator.py`. Linked from `README.md`.
- `DEVELOPMENT_PLAN.md` "Phase Status" table updated: Phase 2 marked
  Complete with a summary; Phase 2 section under "Phased Build Plan"
  expanded with a "Delivered" sub-section; "Next Steps" checklist updated
  (Phase 2 item ticked, Phase 3 item added).
- `README.md` updated: "Status" line, a new "Trying the BCP generator
  (Phase 2)" subsection under Getting Started, and a link to
  `docs/bcp_generation_rules.md` in the Documents list.
- `TESTING.md` updated: expected test count (35 passed), new "Phase 2 —
  tests/test_bcp_generator.py" section describing what the 21 new tests
  cover.
- No changes made to `schema/001_core_schema.sql` or
  `schema/002_rbac_approvals_review_additions.sql` — Phase 2 required no
  schema changes; provenance was solved using the existing
  `document_versions` table instead (see above). `schema/SCHEMA_REVIEW.md`
  was left unchanged accordingly (no new migration to document).

**Known limitations / deviations, disclosed honestly:**

- No PDF/DOCX export of generated BCPs was built in this phase — out of
  scope for FR7/FR8 specifically (that's FR10/NFR10 territory); the
  dashboard shows generated plans read-only, same pattern as Phase 1.
- No UI for hand-editing a generated draft plan was built; data
  entry/editing remains via the MCP tools, consistent with the Phase 1
  brief and the "personal portfolio/demo tool" scope decision in
  `REQUIREMENTS.md`.
- `generate_bau_return_phases` does not gate on `bc_plans.status` (e.g.
  requiring "Invoked" before generating return phases) — the schema has
  no such state machine and none was added, per the "no enterprise
  workflow engine" scope decision; this is documented as deliberate in
  `docs/bcp_generation_rules.md` section 2.

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
