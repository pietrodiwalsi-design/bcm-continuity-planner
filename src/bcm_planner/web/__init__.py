"""Public web workshop tool (second deliverable, alongside the FastMCP
server used via Claude Desktop).

See docs/web_workshop_tool.md for the full design: tenant isolation
model, session/access-code flow, local run instructions, and Render
deployment. This package only ever imports and reuses the existing
Phase 0-5 business logic modules (`bia_engine`, `bcp_generator`,
`crisis_management`, `crisis_communications`) — it never duplicates BIA/
BCP/CMT calculation or generation logic. `mcp_server.py` is untouched by
this package and continues to work unchanged.

Modules:
- `tenancy.py` — the tenant-isolation enforcement layer. Every route in
  `app.py` that reads or writes a specific entity (activity, BIA
  assessment, BC plan, etc.) resolves it through one of this module's
  `get_*` functions rather than calling the underlying business-logic
  module's `get_*` directly, so a mismatched `organization_id` always
  raises `TenantMismatchError` (mapped to a plain HTTP 404) instead of
  silently returning another workshop's data.
- `workshop.py` — creates a new workshop session (organization +
  bootstrap admin `application_users` row + `web_workshop_sessions`
  access-token row) in one transaction.
- `pdf_export.py` — thin WeasyPrint HTML-to-PDF rendering layer for the
  BIA/BCP/CMT report templates under `templates/pdf/`. Contains no
  business logic of its own.
- `app.py` — the FastAPI + Jinja2 application: routes, request/response
  handling, and wiring the above together. Run locally via
  `python -m bcm_planner.web.app` or `uvicorn bcm_planner.web.app:app`;
  see docs/web_workshop_tool.md for full instructions.
"""
