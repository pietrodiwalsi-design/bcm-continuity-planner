# Changelog

All notable changes to this project are documented in this file. Dates are
in `YYYY-MM-DD` format. This file is the chronological record referenced by
the Documentation Governance rules in `DEVELOPMENT_PLAN.md`.

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
