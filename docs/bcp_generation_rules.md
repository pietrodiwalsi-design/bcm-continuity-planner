# BCP & Return-to-BAU Auto-Generation Rules (Phase 2)

This document describes the **rule-based template mapping** used by
`src/bcm_planner/bcp_generator.py` to auto-populate draft Business
Continuity Plans (FR7) and Return-to-BAU phase sets (FR8) from existing
Phase 1 BIA data. This is intentionally **not** AI-generated prose — per
the Phase 2 brief, a deterministic, auditable mapping from structured BIA
data to standard plan content is appropriate for this tool's scope and
gives Peter predictable, explainable draft output to hand-edit.

## 1. BCP draft generation (`generate_bcp_draft_from_bia`)

Given a `bia_id` (an existing `bia_assessments` row) and, implicitly or
explicitly, a `recovery_strategies` row for that BIA:

### 1.1 `bc_plans` field derivation

| Field | Derivation |
|---|---|
| `plan_title` | `"{activity.name} — {plan_tier.title()} Business Continuity Plan"` |
| `plan_owner` | Caller-supplied, else defaults to `activities.activity_owner` |
| `invocation_criteria` | Built from `bia_assessments.rto_hours`, `.mtpd_hours`, `.rpo_hours` (if set), and `.mbco_percentage` — see template below |
| `alternate_facility_details` | Copied from the selected `recovery_strategies.description` **only** when `category` is `hot_standby`, `warm_standby`, or `cold_site` (categories that imply a physical/technical alternate facility); left `NULL` for `active_active`, `work_from_home`, `manual_workaround` |
| `version` | `"0.1-draft"` (auto-generated drafts are versioned distinctly from hand-authored `1.0`) |
| `status` | Always `"Draft"` — auto-generation never marks a plan `Approved` |

**Invocation criteria template:**

> Invoke this plan when disruption to '`{activity.name}`' is expected to
> exceed the Recovery Time Objective (RTO) of `{rto_hours}h` and risks
> breaching the Maximum Tolerable Period of Disruption (MTPD) of
> `{mtpd_hours}h`[, or when data loss risk exceeds the Recovery Point
> Objective (RPO) of `{rpo_hours}h`, if set]. Minimum Business Continuity
> Objective (MBCO): `{mbco_percentage}%` of normal capacity. (Auto-generated
> draft — source BIA: `{bia_id}`, Activity: `{activity_id}`, Recovery
> Strategy: `{strategy_id}` [`{category}`].)

The trailing provenance sentence is a **human-readable** cross-reference.
The **machine-queryable** provenance record is a `document_versions` row
(see §3 below).

### 1.2 Action step generation

Two sources of starter `bcp_action_steps`, appended in this order (numbered
sequentially from `step_number = 1`):

**(a) Recovery-strategy-category template** — one fixed set of 3 steps per
`strategy_category_enum` value:

| Category | Steps generated |
|---|---|
| `active_active` | Confirm active-active failover status → Rebalance load across active sites → Monitor active site performance |
| `hot_standby` | Activate hot standby site → Redirect traffic/operations to standby resources → Validate standby site operational status |
| `warm_standby` | Activate warm standby site → Scale up warm standby resources → Redirect operations to warm standby |
| `cold_site` | Mobilize to cold site → Provision cold site infrastructure → Restore data/systems at cold site |
| `work_from_home` | Activate work-from-home protocol → Verify remote access and tooling → Redirect customer/process interactions to remote channels |
| `manual_workaround` | Initiate manual process procedures → Assign personnel to manual process execution → Track manual process backlog for later reconciliation |

Each step also carries a template `responsible_role` (e.g. "IT Operations
Lead", "Recovery Team Lead", "HR/Remote-Work Coordinator", "Process Owner")
and a `timeframe_offset_minutes` (`0`, `15`/`30`, `60`, `120` depending on
category — cold site provisioning takes longer than a hot-standby
redirect, for example). See `CATEGORY_ACTION_STEP_TEMPLATES` in
`bcp_generator.py` for the exact text — it is the single source of truth;
this table is a summary.

**(b) Resource-dependency steps** — one step per row returned by
`get_activity_resource_dependencies(activity_id)` (Phase 1 data), of the
form "Confirm availability of `{resource_type}` resource: `{resource_name}`",
with `responsible_role` derived from `resource_type`:

| `resource_type` | `responsible_role` |
|---|---|
| `personnel` | HR / Staffing Lead |
| `technology` | IT Operations Lead |
| `facility` | Facilities Lead |
| `equipment` | Facilities/Equipment Lead |
| `supplier` | Vendor Management Lead |
| (unmapped/other) | Recovery Team Lead (fallback) |

### 1.3 Recovery strategy selection

If `recovery_strategy_id` is not explicitly passed to
`generate_bcp_draft_from_bia`, the function uses the `recovery_strategies`
row for that `bia_id` with `is_selected_option = TRUE` (Phase 1's "only one
selected per bia_id" rule). If none is selected, generation fails with a
clear `NotFoundError` rather than guessing.

## 2. Return-to-BAU phase generation (`generate_bau_return_phases`)

Given an existing `bc_plans.plan_id`, generates a fixed, standard 4-phase
`bau_return_procedures` set (rule-based, not derived from BIA parameters —
the BAU return sequence is generic across recovery strategies):

| `phase_number` | `title` | `restoration_type` |
|---|---|---|
| 1 | Verify primary resource restoration | Primary Resource Restoration |
| 2 | Parallel run / validation | Parallel Run And Validation |
| 3 | Cutover to primary | Cutover To Primary |
| 4 | Post-incident review handoff | Post-Incident Review Handoff |

Each phase's `validation_criteria` and `action_steps` text is a fixed
template describing what "done" looks like for that phase and the concrete
steps to get there (see `STANDARD_BAU_RETURN_PHASES` in
`bcp_generator.py`). This function is intended for use once a `bc_plan` has
been invoked/tested and primary resources are being restored — the schema
does not enforce a plan-status gate for this (no DB constraint exists for
it), so it can technically be called against a `Draft` plan too; that is a
deliberate scope choice (no workflow-engine state machine in this tool —
see `REQUIREMENTS.md` scope decision).

## 3. Provenance without schema changes

Per the Phase 2 brief, no columns were added to `bc_plans` or
`bcp_action_steps` to record "this plan was generated from BIA X". Instead,
`generate_bcp_draft_from_bia` writes one row to the existing
`document_versions` table (added in
`schema/002_rbac_approvals_review_additions.sql` for FR15 version
history), with:

- `entity_type = 'bc_plan'`
- `entity_id = <the new plan_id>`
- `version_label = '0.1-draft'`
- `snapshot_json = {"generation_method": "rule_based_auto_populate", "source_bia_id": ..., "source_activity_id": ..., "source_recovery_strategy_id": ...}`
- `change_summary = "Auto-generated draft BCP from BIA + recovery strategy (Phase 2 rule-based generator)."`

`bcp_generator.get_bc_plan_provenance(conn, plan_id)` reads this back
(most recent snapshot for that `plan_id`), returning `None` for
hand-authored plans that were never auto-generated. This reuses an
existing JSONB mechanism instead of introducing a new migration, per the
"prefer solving it within existing columns/JSONB first" rule in
`DEVELOPMENT_PLAN.md`.

No new migration file was needed for Phase 2.
