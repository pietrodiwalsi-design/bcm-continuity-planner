# Crisis Communication Holding Statement Templates (Phase 3)

This document describes the **rule-based template set** used by
`generate_holding_statement_draft()` in
`src/bcm_planner/crisis_communications.py` to auto-populate draft crisis
holding statements (FR10). Same approach as Phase 2's
`docs/bcp_generation_rules.md`: this is intentionally **not** AI-generated
prose — a deterministic, auditable mapping from a `scenario_type` string to
a fixed template gives Peter predictable, explainable draft output to
hand-edit and route through an actual legal review before any dispatch.

## 1. Scope: draft only, never live dispatch

Per `REQUIREMENTS.md`'s scope decision, NFR14 (broadcast alert latency,
live SMS/email/push dispatch) is explicitly deferred for this
personal/demo tool. `generate_holding_statement_draft()` produces **text
only** — it does not send anything, does not integrate with any channel,
and does not mark anything as approved. It is a starting point for a human
to edit and then route through the organization's real crisis
communication sign-off process.

## 2. Placeholder tokens

Every template contains these fill-in-the-blank tokens, meant to be
replaced by a human before any use:

| Token | Meaning |
|---|---|
| `{incident_summary}` | A short, factual description of what is affected (e.g. "our online banking platform", "the downtown branch network") |
| `{expected_resolution_time}` | A specific time/date estimate, or a placeholder like "as soon as possible" if unknown |
| `{contact_channel}` | Where affected stakeholders should go for updates (e.g. a status page URL, a phone number, an email alias) |

Templates are plain Python `str.format()`-style templates; the helper
itself does **not** perform substitution — it returns the template text
with the tokens still in place, so nothing is accidentally dispatched
half-filled.

## 3. Scenario types covered

The template set matches the `scenario_type` examples given directly in
the `message_bank.scenario_type` column comment in
`schema/001_core_schema.sql` ("Power Outage, Cyberattack/DDoS, Public
Transit Disruption, Data Breach"), matched case-insensitively with spaces
and hyphens normalized to underscores (so `"Power Outage"`,
`"power-outage"`, and `"power_outage"` all resolve to the same template):

| `scenario_type` (normalized) | Summary |
|---|---|
| `power_outage` | Generic outage notice — restoration ETA + apology for inconvenience. |
| `cyberattack_ddos` | Security-incident-toned notice — acknowledges investigation/containment in progress, avoids technical detail or speculation. |
| `data_breach` | Same shape as cyberattack, **plus an explicit in-template flag** noting that data breach communications may carry statutory notification obligations and require legal/regulatory review before dispatch — this is the highest-sensitivity template in the set. |
| `public_transit_disruption` | Service-disruption notice framed around journey/service impact rather than technical/security framing. |

Any `scenario_type` not in this table falls back to a **generic** template
(`_GENERIC_HOLDING_STATEMENT_TEMPLATE` in `crisis_communications.py`) that
still contains all three placeholder tokens and an explicit note that it
is a fallback needing tailoring — the helper never silently returns an
empty or incorrect draft for an unrecognized (but still valid, free-text)
`scenario_type`.

See `HOLDING_STATEMENT_TEMPLATES` in `crisis_communications.py` for the
exact template text — it is the single source of truth; this table is a
human-readable summary.

## 4. `[DRAFT — NOT LEGALLY APPROVED]` prefix

Every generated template (including the generic fallback) begins with the
literal text `[DRAFT — NOT LEGALLY APPROVED]`. This is a belt-and-braces
readability signal in the text itself, in addition to the
`pre_approved_by_legal` flag on the returned dict / on any `message_bank`
row it is persisted into.

## 5. `pre_approved_by_legal` is always forced to `False`

This is the safety-critical rule for this phase, called out explicitly in
the Phase 3 brief: **an auto-generated draft must never be represented as
legally approved.**

`generate_holding_statement_draft()` accepts a `pre_approved_by_legal`
keyword argument, but the value returned in the result dict is **always
`False`**, regardless of what the caller passes — the input parameter's
value is discarded. This is enforced **in Python code**, not merely as a
database column default, so it holds even if this helper is ever called
outside of a path that touches the `message_bank` table at all (e.g. an
MCP tool response consumed directly by a chat client, without ever being
persisted).

To actually record that a message has been legally approved, a human must
explicitly call `update_message_bank_entry(..., pre_approved_by_legal=True)`
on an **existing** `message_bank` row, after a real legal review — this is
a separate, explicit, auditable write (logged to `audit_logs` like every
other write in this codebase), never a side effect of drafting.

## 6. Relationship to `message_bank` CRUD

`generate_holding_statement_draft()` is a pure function — it does not
touch the database. To persist a generated draft as a `message_bank` row
(so it shows up in `list_message_bank_entries_by_organization`), pass its
output into `create_message_bank_entry(...)`:

```python
draft = crisis_communications.generate_holding_statement_draft(
    "Data Breach", "Affected Customers"
)
entry = crisis_communications.create_message_bank_entry(
    conn, user_id, organization_id,
    scenario_type=draft["scenario_type"],
    target_audience=draft["target_audience"],
    holding_statement_template=draft["holding_statement_template"],
    pre_approved_by_legal=draft["pre_approved_by_legal"],  # always False
    dispatch_channels=["Email", "Status Page"],
)
```

No new migration was needed for Phase 3 — `message_bank` and
`stakeholder_contact_matrices` were already defined in
`schema/001_core_schema.sql`.
