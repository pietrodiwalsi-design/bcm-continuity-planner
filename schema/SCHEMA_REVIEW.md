# Database Schema Review — Peter's Proposal (2026-09-22)

## Source

Peter supplied `bcp_webtool_schema.sql`, a PostgreSQL 14+ relational schema covering the full BCM scope (BIA, BCP, CMP, exercise/test programme, audit logging). Stored here as `001_core_schema.sql` (unmodified from the original proposal).

## Verdict: Accepted as foundation, with additions

The proposed schema is well-designed and covers nearly all 15 functional requirements from `REQUIREMENTS.md` directly in the data model:

| Requirement | Schema coverage |
|---|---|
| FR1 Scope & hierarchy | `organizations` → `business_units` → `business_processes` → `activities` (self-referencing `parent_activity_id` for sub-activities) |
| FR2 Impact category/thresholds | `impact_categories`, `impact_thresholds` with configurable severity/timeframe/cost bands |
| FR3 MTPD/RTO engine | `bia_assessments.mtpd_hours`/`rto_hours` — **enforced as a DB constraint**: `CHECK (rto_hours < mtpd_hours)` |
| FR4 RPO/MBCO | `bia_assessments.rpo_hours`, `mbco_percentage` |
| FR5 Resource/dependency tracking | `resources` (typed: personnel/technology/facility/equipment/supplier) + `activity_resource_dependencies` join table |
| FR6 Gap analysis & strategy selection | `gap_analyses` (with a `GENERATED ALWAYS AS STORED` computed `gap_hours` column), `recovery_strategies` (typed: active-active/hot/warm/cold/manual) |
| FR7 BCP template builder | `bc_plans` (tiered strategic/tactical/operational) + `bcp_action_steps` |
| FR8 Return to BAU | `bau_return_procedures` |
| FR9 CMT & escalation | `cmt_roles`, `escalation_triggers` |
| FR10 Crisis comms | `stakeholder_contact_matrices`, `message_bank` (with dispatch channel array) |
| FR11-12 Exercise & injects | `exercise_programmes` → `exercises` → `scenario_injects` |
| FR13 Debrief & CAPA | `exercise_debriefs`, `capa_action_items` |
| FR9 (partial) Audit | `audit_logs` (JSONB change payload) |

Notable strong design choices:
- The RTO<MTPD business rule (FR3) is enforced at the database layer via a CHECK constraint, not left to application code — this is the correct place for it.
- `gap_hours` as a generated/stored column avoids drift between stored and calculated values.
- Proper use of ENUM types, JSONB, and array columns where appropriate; FK cascade rules are sensible (e.g., deleting an organization cascades correctly through the hierarchy).
- Indexes cover the join/lookup paths that matter (activity→process, BIA→activity, action steps→plan, etc.).

## Gaps identified and closed

Three requirements from `REQUIREMENTS.md` were **not** adequately covered by the original proposal. These are addressed in `002_rbac_approvals_review_additions.sql`:

1. **NFR1 (RBAC)** — no `application_users`/roles table existed; `audit_logs.user_id` was a free-text VARCHAR with nothing to constrain or check roles against. Added `application_users` with an `app_role_enum` (admin, bia_assessor, approver_process_owner, approver_top_management, crisis_team_member, viewer).

2. **FR14 (multi-tier sign-off workflow)** — the original schema only had single `approved_by`/`approval_date` fields on `bia_assessments`, and `bc_plans` had no approval fields at all. This is one signature, not a multi-tier workflow (process owner → top management) as FR14 requires. Added a generic `sign_off_approvals` table (polymorphic over `bia_assessment` / `bc_plan` / `recovery_strategy` / `crisis_management_plan`) with `sequence_order` to model tiers and a decision/comment/audit trail per tier.

3. **FR15 (review scheduler + version history)** — `bc_plans.version` was just a string with no recurring review cadence or historical snapshots. Added `document_review_schedule` (periodic or event-driven `next_review_date`, one row per governed entity) and `document_versions` (full JSONB snapshot per version label, separate from the generic `audit_logs` diff trail so a specific historical version can be retrieved/exported directly without replaying change diffs).

## Decision: PostgreSQL, not SQLite

The original MVP proposal (see `DEVELOPMENT_PLAN.md` v1) suggested SQLite/JSON to keep the tool lightweight. This schema is Postgres-native: ENUM types, `CHECK` constraints, `GENERATED ALWAYS AS ... STORED` columns, JSONB, and array columns are all used to enforce business rules and structure at the database layer, not in application code.

**Decision:** run PostgreSQL locally via Docker Compose instead of SQLite. This keeps the tool a personal/local setup (no cloud cost, no multi-tenant infra) while preserving the constraint enforcement that makes this schema good — and it demos better for market positioning as a BCM specialist tool. `DEVELOPMENT_PLAN.md` has been updated accordingly.

## Migration files

- `001_core_schema.sql` — Peter's original proposal, unmodified.
- `002_rbac_approvals_review_additions.sql` — RBAC, sign-off workflow, review scheduler, version history additions. Depends on `001` (uses the `uuid-ossp` extension created there).

Apply in order: `001` then `002`.
