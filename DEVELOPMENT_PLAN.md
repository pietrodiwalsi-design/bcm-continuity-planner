# BCM Continuity Planner — High-Level Development Plan

**Owner:** Peter van Walsem
**Purpose:** Personal portfolio/demo tool to establish Peter as a BCM specialist in the market.
**Scope decision (2026-09-22):** Personal use tool, not intended for production deployment within NN Group or any employer. See REQUIREMENTS.md "Scope Decision" section for the full rationale and which NFRs are deferred vs. taken seriously.

## Prior Art

See `REQUIREMENTS.md` → "Prior Art Check" section. No open-source project combines a full BIA engine (MTPD/RTO/RPO/MBCO with enforced business rules) + BCP generator + crisis management + exercise planner. This is a genuine white space for a demo/portfolio tool.

## Architecture

Consistent with Peter's existing IT Risk tool suite (`pqc-cbom-risk-auditor`, `ai-risk-auditor`, `vendor-soc-isae-auditor`, `stride-threat-modeler`):

- **Backend:** Python, FastMCP server — same pattern as the other 4 tools, so it plugs directly into the Enterprise IT Risk MCP Suite / Claude Desktop.
- **Data store:** PostgreSQL 14+, run locally via Docker Compose (revised 2026-09-22, see `schema/SCHEMA_REVIEW.md`). Peter's supplied schema proposal relies on Postgres-native features (ENUM types, CHECK constraints enforcing business rules like RTO<MTPD, `GENERATED ALWAYS AS STORED` columns, JSONB, array columns) that would be lost on SQLite. Still a single-user local setup, no cloud infra or managed cluster — just the right engine for the schema.
- **Frontend:** Standalone HTML dashboard (same pattern as the other repos), no separate frontend build stack.
- **Audit trail:** Append-only JSON log (same evidence-integrity standard used across Peter's other risk tools — absence/ambiguity of evidence is never scored as compliant).
- **Standards alignment baked into the data model from Phase 0:** ISO 22301:2019, ISO/TS 22317, NIST SP 800-34 Rev 1, BCI Good Practice Guidelines 7.0 terminology and structure.

## NFR Approach (v1, portfolio-tool scope)

| NFR from REQUIREMENTS.md | v1 decision |
|---|---|
| RBAC / least privilege | Basic scaffold in Phase 0 (roles as data, not enforced multi-tenant auth) |
| Encryption at rest/in transit (AES-256/TLS 1.3) | Local file encryption for sensitive fields where practical; full TLS 1.3 infra deferred (single-user local/VPS tool) |
| 99.9% SLA, redundant cloud infra | **Deferred** — revisit only if commercial/multi-tenant SaaS intent emerges |
| Offline resilience / exportability | Included — PDF/JSON export of BIA, BCP, CMP outputs (this is also good demo material) |
| Performance <2s calculations | Included — trivial at single-user data scale |
| Multi-tenant, hundreds of business units | **Deferred** |
| Low-stress emergency UX | Included in dashboard design principles, lower priority than BIA engine |
| Standards compliance (ISO 22301, ISO/TS 22317, NIST SP 800-34, BCI GPG 7.0) | **Included from Phase 0** — this is the credibility layer for market positioning |
| Immutable audit logging | Included — append-only JSON log, consistent with Peter's other tools |
| REST APIs / CSV/JSON/PDF/DOCX import-export | Included, scoped to what a demo tool needs (no live HR/CMDB integration) |
| Continuous backups, infra RPO=0 | **Deferred** |
| Modular maintainability | Included — separate modules for BIA engine, plan generators, crisis comms, exercise manager |
| Multilingual/localization | Deferred to post-v1 (NL/EN if revisited) |
| Broadcast alert latency <60s | **Deferred** — no live notification infra planned for v1 |
| Platform DR (ISCP/DRP, RTO<4h/RPO<1h) | **Deferred** — ironic but correct: a demo tool about BCM does not itself need enterprise DR |

## Model Strategy

Consistent with the model routing used across Peter's other IT risk repositories (`pqc-cbom-risk-auditor`, `ai-risk-auditor`, `vendor-soc-isae-auditor`, `stride-threat-modeler`):

| Role | Model | Notes |
|---|---|---|
| Lead coding (schema, BIA engine, FastMCP server, plan generators) | `anthropic/claude-sonnet-5` | Primary build model for all non-trivial implementation work, run via `sessions_spawn` sub-agents per phase. |
| Coding fallback | `anthropic/claude-fable-5` | Used if sonnet-5 is unavailable/in cooldown, per SOUL.md fallback chain. |
| Rapid testing & verification | `google/gemini-3.8-flash` | Fast turn-around for running/checking test suites, linting, and quick sanity passes between sonnet-5 build phases. |
| Requirements/architecture review, documentation | Main session (this assistant) | Requirements review, schema review, documentation upkeep, and the plan itself are done in the main session, not delegated. |

Each phase (see below) is built as a scoped sub-agent task with `anthropic/claude-sonnet-5`, with a clear brief (objective, output, write-scope, verification), and verified with `google/gemini-3.8-flash` before being marked complete in this plan.

## Documentation Governance

To keep documentation on-order as the build progresses (not just at kickoff):

1. **Single source of truth per concern** — `REQUIREMENTS.md` (what/why + scope decisions), `DEVELOPMENT_PLAN.md` (this file — plan, models, phase status), `schema/SCHEMA_REVIEW.md` (data model decisions), and a `CHANGELOG.md` (chronological record of what shipped). No duplicate copies of the same fact across files — cross-reference instead of repeating.
2. **Every phase completion updates three things in the same commit**: (a) tick off the relevant checklist item in "Next Steps" below, (b) mark the phase status in the "Phase Status" table below, (c) add a `CHANGELOG.md` entry. Code and docs land together, never as a follow-up "docs later" commit.
3. **Every schema change** gets a new numbered migration file under `schema/` (never edit `001_core_schema.sql` or `002_...sql` in place) plus a short note appended to `schema/SCHEMA_REVIEW.md` explaining what changed and why — same pattern used for the RBAC/sign-off/review-scheduler additions.
4. **README.md stays the entry point** — it links out to REQUIREMENTS, DEVELOPMENT_PLAN, schema docs, and (once it exists) an API/usage doc; README itself is kept short and not duplicated with detail that belongs in the linked files.
5. **Review cadence**: after each phase, before starting the next, do a 5-minute doc-consistency pass (do REQUIREMENTS.md, DEVELOPMENT_PLAN.md, and the actual repo state agree?) — same discipline as the project's own FR15 (version control & maintenance scheduler), applied to the project's own documentation.

## Phase Status

| Phase | Scope | Status |
|---|---|---|
| 0 | Data model & scope | **Complete** (2026-09-22). Schema (Peter's proposal + RBAC/sign-off/review additions) committed. Repo scaffold complete: `docker-compose.yml` (PostgreSQL 16-alpine, auto-applies 001+002 via docker-entrypoint-initdb.d), `src/bcm_planner/` package (`db.py`, `bia_engine.py`, `mcp_server.py`), `pyproject.toml`/`requirements.txt`, `.gitignore`. |
| 1 | BIA Engine | **Complete** (2026-09-22). FastMCP tools for scope/hierarchy CRUD, impact matrix config, MTPD/RTO/RPO/MBCO capture (RTO<MTPD enforced), gap analysis + SPOF detection, recovery strategy selection ("only one selected" in app logic), RBAC write-gating, and append-only audit logging. 14/14 pytest tests passing against a live Postgres instance. Standalone HTML dashboard (`dashboard/`) with static JSON export (`scripts/export_dashboard_data.py`) verified end-to-end. See `CHANGELOG.md` (2026-09-22 entry) for full detail. |
| 2 | Plan Generators | Not started |
| 3 | Crisis Management | Not started |
| 4 | Exercise & Test Planner | Not started |
| 5 | Governance & Lifecycle | Not started |

## Phased Build Plan

### Phase 0 — Data Model & Scope (Foundation)
- Define entities: Product/Process/Activity BIA hierarchy, Impact Category Matrix, MTPD/RTO/RPO/MBCO records, Resource/Dependency Map, Recovery Strategy.
- Establish ISO 22301 / ISO TS 22317 / NIST SP 800-34 / BCI GPG 7.0 terminology mapping in the schema.
- Basic RBAC role scaffold + append-only audit log foundation.
- Covers FR1–FR2 (partially), sets up everything downstream.

### Phase 1 — BIA Engine (MVP core)
- Scope/hierarchy registration (parent-child, vendor dependency mapping).
- Impact matrix configuration (financial/operational/reputational/legal/regulatory, configurable severity thresholds).
- MTPD → RTO calculator with enforced business rule RTO < MTPD.
- RPO/MBCO capture per critical activity/system.
- Resource & dependency inventory (personnel, applications, facilities, equipment, suppliers).
- Gap analysis: current recovery capability vs. target RTO/RPO, single-point-of-failure detection, recovery strategy selection assist (Active-Active / Hot-Warm-Cold / Manual Workaround).
- Covers FR1–FR6. This is the technically hardest and most unique part — highest priority, best demo value.

### Phase 2 — Plan Generators
- BCP template builder: auto-populate strategic/tactical/operational plans with role-based action steps, invocation criteria, alternate facility procedures — generated from Phase 1 BIA data, not a standalone module.
- Return-to-BAU module: restoration/transition procedures back to primary or new permanent resources.
- Covers FR7–FR8.

### Phase 3 — Crisis Management
- Crisis Management Team (CMT) role definitions (Crisis Comms Lead, Legal Counsel, IT/Security head, etc.) with severity-based escalation triggers.
- Crisis Communication Plan generator: stakeholder contact matrix, pre-approved holding statements/message bank, multi-channel dispatch rules (social, press, internal).
- Covers FR9–FR10. Self-contained enough to build as its own module once Phase 1 data exists.

### Phase 4 — Exercise & Test Planner
- Exercise templates across discussion-based, tabletop, simulation, live, and functional test categories, with pre-built disruption scenarios (power outage, cyberattack, transit disruption).
- Scenario injects and time-phased storyboarding for exercise facilitation.
- Hot-debrief logging, gap capture, CAPA (Corrective/Preventive Action) tracker with owners and deadlines.
- Covers FR11–FR13. Highest "wow factor" for demos but lowest technical priority — only build once Phases 1–3 are solid.

### Phase 5 — Governance & Lifecycle
- Multi-tier sign-off workflow (process owner → top management) for BIA findings, strategies, and published plans.
- Version control and recurring review-cycle scheduler (annual/event-driven), maintenance log across BIA/BCP/CMP/test reports.
- Covers FR14–FR15. Lightweight state machine, no enterprise workflow engine needed.

## Priority Order for Build

1. Phase 0 + Phase 1 (BIA Engine) — the unique, hardest, best-demo part.
2. Phase 2 (Plan Generators) — turns BIA data into deliverable documents.
3. Phase 3 (Crisis Management) — completes the "BCP + CMP" scope from the original ask.
4. Phase 4 (Exercise Planner) — differentiator, build once core is proven.
5. Phase 5 (Governance) — polish layer, build last.

## Next Steps

- [x] Scaffold repo structure (Python package, FastMCP server skeleton, `dashboard/` for standalone HTML). *(Note: `data/` for SQLite/JSON was superseded by the PostgreSQL decision in `schema/SCHEMA_REVIEW.md` — no SQLite/JSON data directory was built.)*
- [x] Implement Phase 0 data model (entities + schema, ISO/NIST/BCI terminology mapping). *(Schema was already committed pre-Phase-0-build; this item closes out the repo-scaffold half of Phase 0.)*
- [x] Implement Phase 1 BIA engine (calculation logic, gap analysis, resource/dependency tracking).
- [x] Build standalone HTML dashboard for BIA entry + gap analysis visualization (demo-facing). *(Read-only demo view via static JSON export, not a data-entry UI — data entry happens via the MCP tools per the Phase 1 brief.)*
- [x] Write test suite for MTPD/RTO/RPO business rule enforcement. *(14 tests in `tests/test_bia_engine.py`, covering RTO<MTPD, hierarchy CRUD, gap analysis, SPOF detection, recovery strategy selection, RBAC, and audit logging — see `TESTING.md`.)*
- [ ] Once Phase 1 is solid: Phase 2 plan generators, then Phase 3 crisis management module.
- [ ] Revisit Phase 4/5 after Peter has used the tool on at least one real (personal/demo) BIA case end-to-end.
