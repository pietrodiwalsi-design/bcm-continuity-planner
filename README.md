# BCM Continuity Planner

Personal portfolio/demo tool for Business Impact Analysis (BIA), Business Continuity Plan (BCP), and Crisis Management Plan (CMP) development — built by Peter van Walsem to establish a market presence as a BCM specialist.

**Status:** Phase 0 (repo scaffold), Phase 1 (BIA Engine), Phase 2 (Plan Generators — BCP builder + Return-to-BAU module), Phase 3 (Crisis Management — CMT roles/escalation mapping + crisis communications/message bank builder), Phase 4 (Exercise & Test Planner — exercise programme/exercise CRUD, disruption scenario templates, scenario inject storyboarding, debrief + CAPA action tracking), and Phase 5 (Governance & Lifecycle — multi-tier sign-off workflow, recurring review-cycle scheduler, version/maintenance log) complete. See the "Phase Status" table in [DEVELOPMENT_PLAN.md](./DEVELOPMENT_PLAN.md).

## Getting Started

Requires Docker + Docker Compose and Python 3.10+.

```bash
# 1. Clone and enter the repo, then copy env defaults
cp .env.example .env

# 2. Start local PostgreSQL — schema/001 and schema/002 are auto-applied
#    on first boot via docker-entrypoint-initdb.d
docker compose up -d
docker compose ps   # wait for STATUS to show "healthy"

# 3. Install the package (editable) and its dependencies
pip install -e .
# or: pip install -r requirements.txt && pip install -e .

# 4. Run the FastMCP server (stdio transport, for Claude Desktop / OpenClaw / etc.)
bcm-mcp
# or: python -m bcm_planner.mcp_server

# 5. Run the test suite (see TESTING.md for full detail)
set -a; . ./.env; set +a
pytest tests/ -v

# 6. Generate the demo dashboard data, then open dashboard/index.html in a browser
python3 scripts/export_dashboard_data.py
```

### Trying the BCP generator (Phase 2)

Once you have an activity with a `bia_assessments` row and a **selected**
`recovery_strategies` row (Phase 1), generate a draft Business Continuity
Plan and its Return-to-BAU phases via the MCP tools
(`bcm_planner.bcp_generator`):

```python
from bcm_planner import db, bcp_generator

with db.get_connection() as conn:
    result = bcp_generator.generate_bcp_draft_from_bia(conn, user_id, bia_id)
    plan_id = result["plan"]["plan_id"]
    bcp_generator.generate_bau_return_phases(conn, user_id, plan_id)
```

This rule-based auto-population (invocation criteria from MTPD/RTO/RPO,
action steps from the recovery strategy category + resource dependencies,
standard 4-phase Return-to-BAU set) is documented in full in
[docs/bcp_generation_rules.md](./docs/bcp_generation_rules.md). The
dashboard's "Business Continuity Plans" section shows generated plans
linked back to their source BIA.

### Trying the Crisis Management module (Phase 3)

CMT roles and escalation triggers are scoped to an `organization_id`
(Phase 0 data). Once you have at least one `cmt_roles` row registered,
the escalation path helper (`bcm_planner.crisis_management`) tells you who
to notify for a given incident severity:

```python
from bcm_planner import db, crisis_management, crisis_communications

with db.get_connection() as conn:
    path = crisis_management.get_escalation_path_for_severity(
        conn, organization_id, "catastrophic"
    )
    # path["triggers"] -> matching escalation_triggers rows
    # path["notified_roles"] -> CMT roles to notify for this severity

    draft = crisis_communications.generate_holding_statement_draft(
        "data_breach", "Affected Customers"
    )
    # draft["holding_statement_template"] contains {incident_summary} /
    # {expected_resolution_time} / {contact_channel} placeholders for a
    # human to fill in; draft["pre_approved_by_legal"] is always False.
```

The severity-based notification heuristic and the full holding-statement
template set are documented in
[docs/crisis_communication_templates.md](./docs/crisis_communication_templates.md).
The dashboard's "Crisis Management Team (CMT) Roster" and "Escalation
Summary" sections show this data read-only; `message_bank` and
`stakeholder_contact_matrices` content is intentionally **not** shown in
the dashboard (draft messaging text / named contacts, not general-demo
appropriate — see `CHANGELOG.md`).

### Trying the Exercise & Test Planner (Phase 4)

Exercises are scoped under an `exercise_programmes` row (per organization,
per year). A disruption scenario template gives you a suggested
scenario_description/category/objectives, and a matching starter set of
time-phased injects, without writing any prose yourself:

```python
import datetime
from bcm_planner import db, exercise_planner, scenario_injects, exercise_debrief

with db.get_connection() as conn:
    programme = exercise_planner.create_exercise_programme(
        conn, user_id, organization_id, "2026 Exercise Programme", 2026, "Objectives."
    )
    template = exercise_planner.get_disruption_scenario_template("cyberattack_ddos")
    exercise = exercise_planner.create_exercise(
        conn, user_id, programme["programme_id"], template["suggested_category"],
        "Cyberattack Tabletop", datetime.date(2026, 10, 1), "Facilitator Name",
        template["suggested_scenario_description"],
    )
    injects = scenario_injects.generate_injects_from_scenario_template(
        conn, user_id, exercise["exercise_id"], "cyberattack_ddos"
    )
    storyboard = scenario_injects.get_exercise_storyboard(conn, exercise["exercise_id"])

    debrief = exercise_debrief.create_exercise_debrief(
        conn, user_id, exercise["exercise_id"], "Hot debrief summary.", overall_rating="Satisfactory"
    )
    exercise_debrief.create_capa_action_item(
        conn, user_id, debrief["debrief_id"], "Gap found.", "Corrective action.",
        "Owner Name", datetime.date(2026, 12, 31),
    )
    overdue = exercise_debrief.get_overdue_capa_items(conn, organization_id)
```

`get_disruption_scenario_template` **raises a clear error** (not a
silent generic fallback) for an unrecognized `scenario_type` —
facilitators should author a bespoke scenario rather than get mismatched
templated content. `get_exercise_storyboard` validates inject
`sequence_number`/`time_offset_minutes` ordering and raises a
`StoryboardValidationError` on a broken timeline. Only one debrief is
permitted per exercise. The full template set, inject design philosophy,
and storyboard validation rules are documented in
[docs/exercise_scenario_templates.md](./docs/exercise_scenario_templates.md).
The dashboard's "Exercises & Tests" and "Open / Overdue CAPA Action
Items" sections show summary counts only — full inject content and
debrief narrative text are intentionally not shown in the demo dashboard.

See [TESTING.md](./TESTING.md) for full test instructions and
[DEVELOPMENT_PLAN.md](./DEVELOPMENT_PLAN.md) for architecture and phase status.

## Scope

This is a personal-use tool, not intended for production deployment within any employer's environment. See [REQUIREMENTS.md](./REQUIREMENTS.md) for the full functional/non-functional requirements and the scope decision, and [DEVELOPMENT_PLAN.md](./DEVELOPMENT_PLAN.md) for the phased build plan.

## Why this exists

A GitHub prior-art check (documented in REQUIREMENTS.md) found no open-source tool combining a full BIA engine (MTPD/RTO/RPO/MBCO with enforced business rules) with BCP generation, crisis management, and exercise planning in one place. This project fills that gap as a demonstrable, standards-aligned (ISO 22301:2019, ISO/TS 22317, NIST SP 800-34 Rev 1, BCI GPG 7.0) tool.

## Architecture (planned)

- Python backend, FastMCP server (consistent with the author's other IT risk tooling: `pqc-cbom-risk-auditor`, `ai-risk-auditor`, `vendor-soc-isae-auditor`, `stride-threat-modeler`)
- PostgreSQL 14+ (local via Docker Compose) — see `schema/`
- Standalone HTML dashboard
- Append-only JSON/DB audit log

## Documents

- [REQUIREMENTS.md](./REQUIREMENTS.md) — Full functional & non-functional requirements, scope decision, prior art check.
- [DEVELOPMENT_PLAN.md](./DEVELOPMENT_PLAN.md) — High-level phased development plan (Phase 0–5) and next steps.
- [docs/bcp_generation_rules.md](./docs/bcp_generation_rules.md) — Rule-based mapping used by the Phase 2 BCP/Return-to-BAU auto-generators (recovery strategy category → action step templates, standard BAU return phases).
- [docs/crisis_communication_templates.md](./docs/crisis_communication_templates.md) — Rule-based holding statement template set used by the Phase 3 `generate_holding_statement_draft` helper, and the escalation-path notification heuristic used by `get_escalation_path_for_severity`.
- [docs/exercise_scenario_templates.md](./docs/exercise_scenario_templates.md) — Rule-based disruption scenario template set used by the Phase 4 `get_disruption_scenario_template` helper, the matching inject templates used by `generate_injects_from_scenario_template`, and the storyboard validation rules used by `get_exercise_storyboard`.
- [docs/governance_lifecycle.md](./docs/governance_lifecycle.md) — The Phase 5 multi-tier sign-off state machine (`create_sign_off_chain`/`submit_sign_off_decision`/`get_sign_off_status`), the `compute_next_review_date` calendar-month heuristic, and the `document_review_schedule`/`document_versions` review-cycle + maintenance-log logic.
- [schema/001_core_schema.sql](./schema/001_core_schema.sql) — Core PostgreSQL schema (Peter's proposal).
- [schema/002_rbac_approvals_review_additions.sql](./schema/002_rbac_approvals_review_additions.sql) — RBAC, sign-off workflow, review scheduler additions.
- [schema/SCHEMA_REVIEW.md](./schema/SCHEMA_REVIEW.md) — Review of the schema proposal against requirements, gaps found and closed.

## License

TBD.
