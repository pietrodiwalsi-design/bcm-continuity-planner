# BCM Continuity Planner

Personal portfolio/demo tool for Business Impact Analysis (BIA), Business Continuity Plan (BCP), and Crisis Management Plan (CMP) development — built by Peter van Walsem to establish a market presence as a BCM specialist.

**Status:** Phase 0 (repo scaffold) and Phase 1 (BIA Engine) complete. See the "Phase Status" table in [DEVELOPMENT_PLAN.md](./DEVELOPMENT_PLAN.md).

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
- [schema/001_core_schema.sql](./schema/001_core_schema.sql) — Core PostgreSQL schema (Peter's proposal).
- [schema/002_rbac_approvals_review_additions.sql](./schema/002_rbac_approvals_review_additions.sql) — RBAC, sign-off workflow, review scheduler additions.
- [schema/SCHEMA_REVIEW.md](./schema/SCHEMA_REVIEW.md) — Review of the schema proposal against requirements, gaps found and closed.

## License

TBD.
