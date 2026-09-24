-- ============================================================================
-- Schema Additions: Impact Matrix (multi-scope), Resource Recovery Measures,
-- Worst-Case Scenario narrative fields.
-- Rationale: Peter's workshop feedback (2026-09-24) —
--   1. BIA needs a worst-case-scenario narrative per product/service,
--      business process, and activity.
--   2. Impact severity (low/medium/high/critical) must be assessable per
--      scope (product/service, process, activity) across 6 timeframes
--      (1h, 4h, 8h, 1 day, 3 days, 1 week) and 4 categories (financial,
--      reputation/customer satisfaction, operational, compliance) — this
--      supersedes/extends the free-form impact_categories/impact_thresholds
--      pair from 001_core_schema.sql with a structured, editable grid.
--   3. Resources need their own recovery measures (backup/redundancy plans),
--      separate from activity-level recovery_strategies.
-- Depends on: 001_core_schema.sql (organizations, products_services,
-- business_processes, activities, resources).
-- ============================================================================

-- ----------------------------------------------------------------------------
-- Worst-case scenario narrative — product/service, process, activity level.
-- ----------------------------------------------------------------------------

ALTER TABLE products_services ADD COLUMN IF NOT EXISTS worst_case_scenario TEXT;
ALTER TABLE business_processes ADD COLUMN IF NOT EXISTS worst_case_scenario TEXT;
ALTER TABLE activities ADD COLUMN IF NOT EXISTS worst_case_scenario TEXT;

-- ----------------------------------------------------------------------------
-- Structured impact matrix: scope x category x timeframe -> severity.
-- Polymorphic scope (product_service / business_process / activity) kept as
-- (scope_type, scope_id) rather than three nullable FK columns, matching the
-- existing sign_off_approvals pattern in 002_rbac_approvals_review_additions.sql
-- (entity_type + entity_id, enforced at application layer).
-- ----------------------------------------------------------------------------

CREATE TYPE impact_scope_enum AS ENUM ('product_service', 'business_process', 'activity');
CREATE TYPE bia_impact_matrix_category_enum AS ENUM ('financial', 'reputation_customer', 'operational', 'compliance');
CREATE TYPE bia_impact_severity_enum AS ENUM ('low', 'medium', 'high', 'critical');

CREATE TABLE impact_matrix_entries (
    entry_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    scope_type impact_scope_enum NOT NULL,
    scope_id UUID NOT NULL,
    category bia_impact_matrix_category_enum NOT NULL,
    -- Timeframe in hours: 1, 4, 8, 24 (1 day), 72 (3 days), 168 (1 week).
    timeframe_hours INT NOT NULL,
    severity bia_impact_severity_enum NOT NULL,
    notes TEXT,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (scope_type, scope_id, category, timeframe_hours)
);

CREATE INDEX idx_impact_matrix_scope ON impact_matrix_entries(scope_type, scope_id);

-- ----------------------------------------------------------------------------
-- Resource recovery measures — mitigations/backup arrangements attached
-- directly to a resource (distinct from activity-level recovery_strategies).
-- ----------------------------------------------------------------------------

CREATE TYPE resource_recovery_measure_status_enum AS ENUM ('not_started', 'planned', 'in_place');

CREATE TABLE resource_recovery_measures (
    measure_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    resource_id UUID NOT NULL REFERENCES resources(resource_id) ON DELETE CASCADE,
    measure_type VARCHAR(100) NOT NULL, -- e.g. backup/redundant unit, alternate supplier, cross-trained staff, spare equipment, failover site, manual workaround
    description TEXT NOT NULL,
    recovery_time_hours INT,
    status resource_recovery_measure_status_enum NOT NULL DEFAULT 'not_started',
    owner VARCHAR(255),
    estimated_cost NUMERIC(15, 2),
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_resource_recovery_measures_resource ON resource_recovery_measures(resource_id);
