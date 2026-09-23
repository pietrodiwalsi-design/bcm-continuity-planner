-- ============================================================================
-- Web Workshop Tool: per-client session access mapping
-- Rationale: the new public web workshop tool (docs/web_workshop_tool.md,
-- src/bcm_planner/web/) needs a simple, cost-free way to map a short URL
-- access token to exactly one organizations row (one workshop client = one
-- organization) without a full user-login system, while keeping every
-- subsequent web-layer query scoped to that organization_id — see
-- docs/web_workshop_tool.md "Tenant Isolation Model" for the enforcement
-- mechanism (src/bcm_planner/web/tenancy.py).
--
-- This is additive only: no existing table (001/002/003) is altered. The
-- MCP server (src/bcm_planner/mcp_server.py) and Claude Desktop access mode
-- do not use this table at all and are unaffected by it.
--
-- Depends on: 001_core_schema.sql (organizations),
--             002_rbac_approvals_review_additions.sql (application_users)
-- ============================================================================

CREATE TABLE web_workshop_sessions (
    session_token VARCHAR(64) PRIMARY KEY,  -- URL-safe random token (secrets.token_urlsafe), e.g. /session/<token>/...
    organization_id UUID NOT NULL UNIQUE REFERENCES organizations(organization_id) ON DELETE CASCADE,
    admin_user_id UUID NOT NULL REFERENCES application_users(user_id) ON DELETE CASCADE, -- bootstrap RBAC identity used for all writes made through the web UI for this workshop
    workshop_label VARCHAR(255), -- optional human-friendly label Peter can set (e.g. client name / date), never shown across sessions
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

-- organization_id is UNIQUE above (one workshop session per organization by
-- design in this phase — see docs/web_workshop_tool.md), so this index is
-- mainly useful for the reverse lookup (organization -> its session token).
CREATE INDEX idx_web_workshop_sessions_org ON web_workshop_sessions(organization_id);

-- ----------------------------------------------------------------------------
-- Migration tracking (schema_migrations)
-- ----------------------------------------------------------------------------
-- Used by scripts/run_migrations.py, the startup migration runner needed
-- for Render managed Postgres (which does not support mounting
-- docker-entrypoint-initdb.d/* the way local docker-compose does — see
-- docs/web_workshop_tool.md "Deploying on Render"). Local docker-compose
-- continues to use docker-entrypoint-initdb.d for a *fresh* volume; this
-- table lets the same numbered schema/*.sql files be applied idempotently
-- against an already-existing database too (both on Render and against an
-- existing local dev volume that predates this migration).
CREATE TABLE IF NOT EXISTS schema_migrations (
    version VARCHAR(50) PRIMARY KEY,
    applied_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);
