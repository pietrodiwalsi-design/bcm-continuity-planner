# Exercise Scenario Templates & Storyboard Injects (Phase 4)

This document describes the **rule-based template sets** used by
`get_disruption_scenario_template()` in
`src/bcm_planner/exercise_planner.py` and
`generate_injects_from_scenario_template()` in
`src/bcm_planner/scenario_injects.py` to auto-populate exercise scenario
descriptions and time-phased storyboard injects (FR11/FR12). Same
approach as Phase 2's `docs/bcp_generation_rules.md` and Phase 3's
`docs/crisis_communication_templates.md`: this is intentionally **not**
AI-generated prose — a deterministic, auditable mapping from a
`scenario_type` string to fixed template content gives Peter predictable,
explainable starting material for exercise design, to be reviewed and
tailored by an actual facilitator before use.

## 1. Scope: exercise design aid, not a live facilitation tool

Per `REQUIREMENTS.md`'s scope decision, this phase is data model +
rule-based content generation + tracking only — there is no live
facilitation/broadcast software, no real-time multi-user exercise-running
UI. These templates produce **starter content** (a scenario description +
suggested category + suggested objectives, or a set of storyboard
injects) that a human facilitator reviews, edits, and uses when actually
running an exercise (which happens outside this tool).

## 2. `scenario_type` naming — aligned with Phase 3 where sensible

`get_disruption_scenario_template()` and
`generate_injects_from_scenario_template()` both use
`exercise_planner.normalize_scenario_type()` (case/space/hyphen/slash
insensitive, same normalization rule as
`crisis_communications.generate_holding_statement_draft`) to resolve a
`scenario_type` string to a canonical, underscore-separated key.

Four of the five scenario types intentionally reuse the same canonical
key as `crisis_communications.HOLDING_STATEMENT_TEMPLATES` from Phase 3,
so the *same* `scenario_type` string can drive both a crisis
communication holding-statement draft and an exercise scenario/inject set
for the same underlying disruption:

| `scenario_type` (normalized) | Also used by Phase 3 `generate_holding_statement_draft`? |
|---|---|
| `power_outage` | Yes |
| `cyberattack_ddos` | Yes |
| `data_breach` | Yes |
| `public_transit_disruption` | Yes |
| `key_supplier_failure` | **No — Phase 4 only.** FR11 explicitly calls out "key supplier failure" as a disruption template type; there was no equivalent need in Phase 3's message-bank scenario set, so this key has no Phase 3 counterpart. |

## 3. Disruption scenario templates (`get_disruption_scenario_template`)

Each entry returns a dict with:

- `scenario_type` — the normalized key.
- `suggested_scenario_description` — starter text for the exercise's
  `scenario_description` field.
- `suggested_category` — one of `exercise_category_enum`'s values
  (`discussion_based` / `scenario_tabletop` / `simulation` / `live` /
  `functional_test`), reflecting the kind of exercise best suited to
  that disruption type in this template set (a judgment call a
  facilitator can override).
- `suggested_objectives` — a list of 2-3 suggested exercise objectives.

| `scenario_type` | Suggested category | Summary |
|---|---|---|
| `power_outage` | `scenario_tabletop` | Extended site power loss; tests invocation criteria, backup power/alternate-site activation, and crisis communication timing. |
| `cyberattack_ddos` | `simulation` | Ransomware/DDoS incident; tests IT/Security → CMT escalation handoff, manual workarounds, and the Phase 3 holding-statement-then-legal-review workflow. |
| `data_breach` | `scenario_tabletop` | Data exfiltration; tests IT/Security + Legal Counsel + CMT Leader coordination on regulatory notification timing and the legal-review gate on communications. |
| `public_transit_disruption` | `discussion_based` | Staff unable to reach site (no physical damage); tests remote-working continuity and MBCO adequacy with reduced on-site headcount. |
| `key_supplier_failure` | `functional_test` | A BIA-flagged single-point-of-failure supplier fails to deliver; tests the recovery strategy's alternate-supplier assumptions against resource dependency data. |

See `DISRUPTION_SCENARIO_TEMPLATES` in `exercise_planner.py` for the exact
template text — it is the single source of truth; this table is a
human-readable summary.

## 4. Unknown `scenario_type` → explicit error, not a generic fallback

Unlike Phase 3's `generate_holding_statement_draft()` (which falls back
to a generic template for any unrecognized `scenario_type`, because a
holding statement must always be producible even for an ad-hoc,
unclassified incident), `get_disruption_scenario_template()` and
`generate_injects_from_scenario_template()` **deliberately raise a clear
`BCMPlannerError`** for an unrecognized `scenario_type`, listing the known
scenario types.

Rationale: an exercise scenario or a storyboard inject set is much more
specific, detailed content than a short holding statement — fabricating
a plausible-sounding but ungrounded scenario/inject set for an
unrecognized disruption type risks misleading a facilitator into using
content that does not actually match their intended exercise. It is
safer and more honest to require the facilitator to author a bespoke
scenario/inject set directly (`create_exercise()` /
`create_scenario_inject()`) when no template exists for their disruption
type.

## 5. Storyboard injects (`generate_injects_from_scenario_template`)

For each of the five scenario types above,
`INJECT_TEMPLATES_BY_SCENARIO` in `scenario_injects.py` defines a starter
set of 4-5 time-phased injects: `(sequence_number, time_offset_minutes,
inject_title, inject_content, delivery_method, expected_team_action)`.

Example — `cyberattack_ddos` (5 injects):

| T+ (min) | Seq | Inject title | Expected team action (summary) |
|---|---|---|---|
| 0 | 1 | Initial detection alert | Triage alert; assess escalation trigger criteria |
| 15 | 2 | IT confirms ransomware encryption spreading | Isolate systems; invoke CMT escalation path |
| 30 | 3 | Law enforcement notification consideration raised | Legal Counsel + CMT Leader assess notification obligations |
| 45 | 4 | Media inquiry received | Route to Spokesperson; legal review before using holding statement |
| 90 | 5 | Ransom note discovered | Escalate to Legal Counsel/CMT Leader; no direct engagement with demand |

The other four scenario types (`power_outage`, `data_breach`,
`public_transit_disruption`, `key_supplier_failure`) each have their own
4-inject starter sets — see `INJECT_TEMPLATES_BY_SCENARIO` in
`scenario_injects.py` for the exact text; this document does not
duplicate every inject in full here (single source of truth is the code).

`generate_injects_from_scenario_template(exercise_id, scenario_type)`
writes these as `scenario_injects` rows linked to `exercise_id`, in
generation order (already time-ordered by construction, so the resulting
storyboard passes `get_exercise_storyboard()`'s validation — see section
6).

## 6. Storyboard ordering validation (`get_exercise_storyboard`)

`scenario_injects.get_exercise_storyboard(exercise_id)` returns the
time-ordered inject list for an exercise (`ORDER BY
time_offset_minutes`), but first validates that:

- No two injects share the same `sequence_number` (ambiguous authoring
  order), and
- `sequence_number` strictly increases as `time_offset_minutes`
  increases (an inject scheduled later in the timeline must not be
  numbered before one scheduled earlier).

If either check fails, `StoryboardValidationError` (a `BCMPlannerError`
subclass) is raised with a message identifying the offending
`sequence_number`/`time_offset_minutes` pair — a facilitator authoring
error is surfaced clearly rather than silently returning a broken
storyboard timeline. This validation runs on every read via
`get_exercise_storyboard()`, not just at creation time, so it also
catches ordering problems introduced later via
`update_scenario_inject()`.

## 7. Relationship to CRUD

Both `get_disruption_scenario_template()` and
`generate_injects_from_scenario_template()`'s *lookup* halves are pure
functions — `get_disruption_scenario_template()` does not touch the
database at all. `generate_injects_from_scenario_template()` does write
`scenario_injects` rows (it needs an existing `exercise_id` to link
them to), but the scenario *template* lookup portion it delegates to is
the same pure mapping as `get_disruption_scenario_template()`.

Typical flow:

```python
from bcm_planner import exercise_planner, scenario_injects

template = exercise_planner.get_disruption_scenario_template("cyberattack_ddos")
exercise = exercise_planner.create_exercise(
    conn, user_id, programme_id,
    category=template["suggested_category"],
    title="Q3 Ransomware Tabletop",
    planned_date=date(2026, 9, 30),
    lead_facilitator="Jane Doe",
    scenario_description=template["suggested_scenario_description"],
)
injects = scenario_injects.generate_injects_from_scenario_template(
    conn, user_id, exercise["exercise_id"], "cyberattack_ddos"
)
storyboard = scenario_injects.get_exercise_storyboard(conn, exercise["exercise_id"])
```

No new migration was needed for Phase 4 — `exercise_programmes`,
`exercises`, `scenario_injects`, `exercise_debriefs`, and
`capa_action_items` were already defined in
`schema/001_core_schema.sql`.
