# Governance & Lifecycle — Sign-Off Workflow and Review Scheduler (Phase 5)

This document describes the two rule-based state machines implemented in
`src/bcm_planner/governance.py` for Phase 5 (FR14-FR15): a **multi-tier
sign-off workflow** for BIA findings, recovery strategies, plans, and
exercise debrief (test) reports, and a **version-control + recurring
review-cycle scheduler** covering the same set of governed entities. Same
"rule-based, deliberately simple, documented heuristic" approach as
Phase 3's `get_escalation_path_for_severity` and Phase 4's scenario
templates — this is **not** an enterprise workflow engine, there is no
configurable BPMN-style routing, no notifications/reminders sub-system,
and no delegation/out-of-office handling. It is a small, auditable state
machine on top of the `sign_off_approvals`, `document_review_schedule`,
and `document_versions` tables already defined in
`schema/002_rbac_approvals_review_additions.sql`.

## 1. Governed entity types

Both state machines operate over the same `approval_entity_enum` used by
`sign_off_approvals`, `document_review_schedule`, and
`document_versions` (all three tables share this enum as their
`entity_type` column type — see `schema/002_...sql`):

| `entity_type` | Backing table | Existence check |
|---|---|---|
| `bia_assessment` | `bia_assessments` (PK `bia_id`) | Enforced |
| `bc_plan` | `bc_plans` (PK `plan_id`) | Enforced |
| `recovery_strategy` | `recovery_strategies` (PK `strategy_id`) | Enforced |
| `exercise_debrief` | `exercise_debriefs` (PK `debrief_id`) | Enforced |
| `crisis_management_plan` | *(no single backing table yet)* | **Skipped** |

`crisis_management_plan` is accepted as a valid `entity_type` string (the
enum value already existed in migration 002, unused until now) but has no
existence check performed against it, since Phase 3 modeled crisis
management as several tables (`cmt_roles`, `escalation_triggers`,
`stakeholder_contact_matrices`, `message_bank`) rather than one
`crisis_management_plan` row with a single PK. Callers passing
`entity_type="crisis_management_plan"` get sign-off/review/version
tracking against an `entity_id` of their own choosing (e.g. a
organization-scoped UUID they mint themselves to represent "the CMP for
this org"), with no automatic validation that it corresponds to anything
concrete. This mirrors the existing codebase's precedent for
documenting scope gaps explicitly rather than fabricating a check
that doesn't map to real schema.

### Migration 003 — adding `exercise_debrief` to `approval_entity_enum`

`schema/002_...sql`'s `approval_entity_enum` originally defined 4 values:
`bia_assessment`, `bc_plan`, `recovery_strategy`,
`crisis_management_plan` — no value existed for exercise/test reports,
even though FR15 explicitly requires a "maintenance log" covering "BIA,
BCP, CMP, **test reports**". `schema/003_governance_test_report_entity_type.sql`
adds the missing enum value:

```sql
ALTER TYPE approval_entity_enum ADD VALUE IF NOT EXISTS 'exercise_debrief';
```

This is deliberately a **standalone single-statement migration file**,
not bundled with any other DDL: PostgreSQL does not allow `ALTER TYPE ...
ADD VALUE` to run inside a transaction block alongside other statements
that might use the new value in the same transaction (pre-PG12) and,
more importantly, `docker-entrypoint-initdb.d` scripts each run in their
own transaction by default via `psql`'s script-runner, so keeping this as
its own file avoids any ordering/transaction subtlety entirely. No other
schema changes were needed — `sign_off_approvals`, `document_review_
schedule`, and `document_versions` (with all their columns, including
`sequence_order`, `required_role`, `decision`, `review_frequency_months`,
`review_trigger_type`, `version_label`, `snapshot_json`) already fully
covered FR14-FR15's requirements as designed in migration 002. See
`schema/SCHEMA_REVIEW.md` for the accompanying note.

## 2. Sign-off workflow (FR14)

### 2.1 Chain creation — `create_sign_off_chain`

A **chain** is the ordered set of `sign_off_approvals` rows for one
`(entity_type, entity_id)` pair, each row a "tier" with a
`sequence_order` (1, 2, 3, ...) and a `required_role` (the
`application_users.role` value permitted to decide that tier).

`STANDARD_SIGN_OFF_TIERS` is the default two-tier chain used when no
`tiers` argument is supplied — matching the phrase "process owner → top
management" from both `REQUIREMENTS.md` FR14 and the Phase 5 brief:

| `sequence_order` | `required_role` |
|---|---|
| 1 | `approver_process_owner` |
| 2 | `approver_top_management` |

Both role names were already defined by migration 002's `user_role_enum`
extension — no schema change needed for RBAC roles either. Callers with a
more complex approval structure (e.g. a 3-tier chain adding a compliance
sign-off) can pass a custom `tiers` list of `{"sequence_order": int,
"required_role": str}` dicts instead; every `required_role` in a custom
chain is still validated to be a member of `user_role_enum` via the same
DB-constraint-translation pattern used elsewhere in the codebase (a
`CHECK`/enum violation surfaces as a clear `BCMPlannerError`, not a raw
psycopg exception).

Creating a chain when one already exists for that entity raises
`DuplicateSignOffChainError` — a chain is created once per entity per
"version" of the sign-off cycle; see §2.4 for restarting one.

### 2.2 Sequential blocking — `submit_sign_off_decision`

The core rule (directly from the Phase 5 brief: "block progression until
prior tier signs off"): a tier's decision can only be submitted once
*every earlier tier* (`sequence_order` strictly less than the target
tier's) has a `decision` of `approved`. Any earlier tier still `pending`
(or in any other non-`approved` state) blocks the later tier, raising
`SignOffSequenceError` naming the specific blocking tier's
`sequence_order` and `required_role`.

`decision` must be one of the same four `sign_off_decision_enum` values
already defined in migration 002: `pending` (the default, not a valid
value to *submit*), `approved`, `rejected`, `returned_for_revision`.
Submitting anything else raises a clear `BCMPlannerError` before ever
reaching the database.

Once a tier has been decided (`decision != 'pending'`), submitting again
for that same tier raises `SignOffAlreadyDecidedError` — decisions are
**one-shot**; changing your mind after all requires
`restart_sign_off_chain()` (§2.4), which is itself an explicit, audited,
`WRITE_ROLES`-gated action rather than a silent overwrite.

### 2.3 RBAC — tier-specific role, not the generic `WRITE_ROLES` set

Unlike most other write operations in this codebase (which gate on
`bia_engine.WRITE_ROLES`, a broad set of "can edit BCM data" roles),
`submit_sign_off_decision()` gates on the **specific `required_role`
of the target tier itself** (or `admin`, which — consistent with every
other module — can act at any tier). A `bia_assessor` who is in
`WRITE_ROLES` cannot decide a tier whose `required_role` is
`approver_top_management`; only a user with that exact role (or
`admin`) can. This is deliberate: sign-off authority is meant to be
narrower and more specific than general edit authority, and conflating
the two would undermine the entire point of a segregated,
audited approval chain. `create_sign_off_chain` and
`restart_sign_off_chain` themselves *do* use the generic `WRITE_ROLES`
gate (creating/resetting the chain's structure is an administrative
action, not a sign-off decision).

### 2.4 Restarting a chain — `restart_sign_off_chain`

After a `rejected` or `returned_for_revision` decision and the
underlying document has been revised, `restart_sign_off_chain()` resets
every tier back to `decision = 'pending'`, `approver_user_id = NULL`,
`decision_date = NULL`, `comments = NULL` in a single statement (all
tiers for the entity, one `UPDATE ... WHERE entity_type = ... AND
entity_id = ...`). This is the only supported way to re-run a sign-off
cycle — there is no automatic re-trigger on document edit, since this
module has no visibility into what "the underlying document changed"
means for every entity type; that judgment is left to the human calling
`restart_sign_off_chain()` once they've made their revision (this mirrors
Phase 4's decision to keep `capa_action_items` status transitions manual
rather than inferring them).

### 2.5 Overall status — `get_sign_off_status`

A read-only convenience wrapper computing one of five `overall_status`
values from the tier list, used by the dashboard (§4) and by callers who
just want a single "where does this stand" answer:

| `overall_status` | Condition |
|---|---|
| `not_started` | No chain exists for this entity yet |
| `rejected` | Any tier's `decision = 'rejected'` |
| `returned_for_revision` | No tier rejected, but any tier `decision = 'returned_for_revision'` |
| `approved` | Every tier's `decision = 'approved'` |
| `in_progress` | None of the above — at least one tier still `pending`, none rejected/returned |

`get_current_pending_tier()` (used internally by `get_sign_off_status`
and separately exposed as its own MCP tool) returns the lowest
`sequence_order` tier still `pending` — the next actionable gate — or
`None` if every tier has been decided.

## 3. Version control & review-cycle scheduler (FR15)

### 3.1 `compute_next_review_date` — the calendar-month heuristic

A pure function (no DB access), `compute_next_review_date(base_date,
review_frequency_months)` adds `review_frequency_months` calendar months
to `base_date` using only the Python standard library's `calendar`
module (no `python-dateutil` dependency added — same "no new dependency
if the stdlib already covers it" discipline as the rest of the
codebase). Because calendar months have varying lengths, the day-of-month
is **clamped** to the last valid day of the target month where it would
otherwise be invalid:

```
2026-01-31 + 1 month  -> 2026-02-28   (Feb 2026 is not a leap year)
2027-01-31 + 13 months -> 2028-02-29  (Feb 2028 IS a leap year)
2026-03-15 + 12 months -> 2027-03-15  (no clamping needed)
```

`review_frequency_months` must be a positive integer; zero or negative
raises a clear `BCMPlannerError` rather than silently producing a
nonsensical "review in the past" date.

### 3.2 `document_review_schedule` CRUD

One `document_review_schedule` row per `(entity_type, entity_id)` (the
same `UNIQUE(entity_type, entity_id)` constraint pattern used elsewhere;
attempting to create a second schedule for an already-scheduled entity
raises a clear `BCMPlannerError`, translated from the underlying
`UniqueViolation` via the same SAVEPOINT pattern used throughout the
codebase).

Two `review_trigger_type` values (`review_trigger_type_enum` from
migration 002), matching REQUIREMENTS.md FR15's "annual/event-driven"
phrasing exactly:

- **`periodic`** (the default): `next_review_date` is auto-computed via
  `compute_next_review_date()` from `last_reviewed_date` (or today, if no
  prior review is recorded yet) + `review_frequency_months` (default 12,
  i.e. annual — REQUIREMENTS.md's default cadence) if not supplied
  explicitly.
- **`event_driven`**: no automatic date computation is possible (there is
  no "frequency" for an ad-hoc, triggered review) — `next_review_date`
  **must** be supplied explicitly, and omitting it raises a clear
  `BCMPlannerError` naming the requirement. `trigger_event_description`
  is an optional free-text field explaining what triggered the review
  (e.g. "Major incident triggered an ad-hoc review").

`update_review_schedule()` allows updating any mutable field
(`review_frequency_months`, `last_reviewed_date`, `next_review_date`,
`review_trigger_type`, `trigger_event_description`, `notes`) via
keyword arguments — `entity_type`/`entity_id` are immutable post-creation
(same "identity fields are not editable" convention as
`bia_engine.update_bia_assessment` for `activity_id`). Calling it with no
fields, or with an unrecognized field name, raises a clear
`BCMPlannerError` before touching the database.

`mark_review_completed()` is the convenience action for "a review just
happened": it sets `last_reviewed_date` (default: today) and advances
`next_review_date` — auto-computed for `periodic` schedules from the new
`last_reviewed_date` + the schedule's `review_frequency_months`, or
(same rule as creation) must be supplied explicitly for `event_driven`
ones.

### 3.3 Upcoming / overdue review queries

Two read-only list functions, used by the dashboard's Governance section
(§4):

- `list_upcoming_review_schedules(within_days=90)` — schedules whose
  `next_review_date` falls between today and today + `within_days`
  (inclusive), ordered soonest-first. 90 days is a judgment-call default
  (a quarter's notice), overridable per call.
- `get_overdue_review_schedules()` — schedules whose `next_review_date`
  is strictly before today, ordered most-overdue-first.

### 3.4 `document_versions` — the maintenance/version log

`create_document_version(entity_type, entity_id, version_label,
snapshot_json, change_summary)` writes a full JSONB snapshot of an
entity's state at a point in time, tagged with a human-assigned
`version_label` string (free-form — e.g. `"1.0"`, `"1.1"`, `"2.0"`, but
no format is enforced beyond `UNIQUE(entity_type, entity_id,
version_label)`, translated to a clear `DuplicateVersionLabelError`
naming the conflicting label). This is the **same underlying table**
Phase 2's `bcp_generator._record_bc_plan_provenance()` already writes to
for auto-generation provenance — Phase 5 does not duplicate or replace
that usage, it adds the general-purpose, CRUD-facing entry point that
spans every governed entity type (BIA/BCP/recovery strategy/exercise
debrief), not just auto-generated `bc_plans`.

`created_at` is set explicitly via `clock_timestamp()` in the INSERT
rather than relying on the column's `DEFAULT CURRENT_TIMESTAMP`:
`CURRENT_TIMESTAMP` resolves to the enclosing *transaction's* start time
in Postgres, so two versions created back-to-back within the same
connection/transaction (a realistic pattern — see
`record_maintenance_update`'s auto-increment tests) would otherwise get
an identical timestamp, making "most recent version" ordering
non-deterministic between ties. `clock_timestamp()` returns true
wall-clock time at statement execution instead, fixing the ordering
without any schema change.

`record_maintenance_update(entity_type, entity_id, change_summary,
snapshot_json=None, version_label=None)` is FR15's actual "maintenance
log" convenience wrapper: it auto-computes the next `version_label` if
one isn't supplied, via `_next_minor_version_label()` — a small
heuristic that parses the latest existing version's label as
`"{major}.{minor}"`, increments `minor` by 1 (`"1.0"` → `"1.1"` → `"1.2"`,
etc.), and falls back to `"1.0"` if there is no prior version, or if the
latest label doesn't parse as `major.minor` (e.g. a caller previously
used a non-numeric label — the heuristic degrades to `"1.0"` rather than
raising, since guessing a "sensible next label" for an unparseable
scheme isn't something a simple rule should attempt). Callers wanting an
explicit major-version bump (e.g. `"2.0"`) pass `version_label`
explicitly, which always overrides the auto-increment.

`list_document_versions_for_entity()` returns every version for an
entity, newest first (`ORDER BY created_at DESC`).
`get_latest_document_version()` returns just the most recent one, or
`None` if the entity has never been versioned (not an error — an
unversioned entity is a normal, expected state, same "return None for an
absent-but-valid state" convention as
`get_review_schedule_for_entity()`).

## 4. Dashboard integration

`scripts/export_dashboard_data.py` exports, in addition to the Phase 1-4
data already exported:

- `sign_off_approvals` (every tier row) and `sign_off_status_summary` (a
  compact per-entity `overall_status` + current-tier summary, computed
  with the exact same heuristic as `get_sign_off_status()`, without
  re-invoking the module function once per entity against a fresh
  connection — the export script already has the full table dumped, so
  it derives the summary from that in-memory).
- `review_schedules` (every row), plus the pre-split
  `upcoming_review_schedules` / `overdue_review_schedules` buckets (same
  90-day default window as `list_upcoming_review_schedules()`).
- `document_versions` (every row) plus `latest_version_by_entity` (a
  `{entity_type:entity_id -> latest row}` map) for a compact "current
  version" column next to each review-schedule row in the dashboard.

Unlike Phase 3's deliberate exclusion of `message_bank` /
`stakeholder_contact_matrices` content (sensitive contact details / draft
messaging text not meant for a general read-only demo view), sign-off
comments and version `snapshot_json` **are** included in this export:
sign-off decisions and version history are themselves the point of a
governance dashboard view, not incidental sensitive content.

`dashboard/index.html` / `dashboard/app.js` render two new sections:
**Governance — Sign-Off Status** (one row per entity with a chain,
overall status pill, and the current pending tier if any) and
**Governance — Document Review Schedule** (two tables: Overdue Reviews
and Upcoming Reviews, each showing the entity, next review date, trigger
type, and current version label).

## 5. Relationship to CRUD — typical flow

```python
from bcm_planner import bia_engine, governance

bia = bia_engine.create_bia_assessment(
    conn, user_id, activity_id, "Jane Doe", date.today(), mtpd_hours=48, rto_hours=24,
)

# 1. Kick off the standard 2-tier sign-off chain for this BIA.
chain = governance.create_sign_off_chain(conn, user_id, "bia_assessment", bia["bia_id"])

# 2. Process owner signs off tier 1.
governance.submit_sign_off_decision(
    conn, process_owner_user_id, "bia_assessment", bia["bia_id"], 1, "approved",
)

# 3. Top management signs off tier 2 — only possible now that tier 1 is approved.
governance.submit_sign_off_decision(
    conn, top_mgmt_user_id, "bia_assessment", bia["bia_id"], 2, "approved",
)

status = governance.get_sign_off_status(conn, "bia_assessment", bia["bia_id"])
assert status["overall_status"] == "approved"

# 4. Schedule an annual review, starting from today.
schedule = governance.create_review_schedule(
    conn, user_id, "bia_assessment", bia["bia_id"], review_frequency_months=12,
)

# 5. A year later, the review happens — log it and advance the schedule.
governance.mark_review_completed(conn, user_id, schedule["schedule_id"])

# 6. Record the maintenance update itself as a new version.
governance.record_maintenance_update(
    conn, user_id, "bia_assessment", bia["bia_id"],
    "Annual review: RTO revised from 24h to 18h following infrastructure upgrade.",
    snapshot_json={"rto_hours": 18},
)
```

Migration 003 (`schema/003_governance_test_report_entity_type.sql`) was
the only schema change needed for Phase 5 — `sign_off_approvals`,
`document_review_schedule`, and `document_versions`, with every column
Phase 5 needed, were already fully defined in
`schema/002_rbac_approvals_review_additions.sql`.
