-- ============================================================================
-- Schema Additions: RBAC, Multi-Tier Sign-Off Workflow, Document Review Scheduling
-- Rationale: closes gaps identified in SCHEMA_REVIEW.md against NFR1 (RBAC),
-- FR14 (management review & digital sign-off), FR15 (version control &
-- maintenance scheduler) that were not covered by 001_core_schema.sql.
-- Depends on: 001_core_schema.sql (uses uuid-ossp extension already created there)
-- ============================================================================

-- ----------------------------------------------------------------------------
-- 7. APPLICATION USERS & RBAC (NFR1: Role-Based Access Control)
-- ----------------------------------------------------------------------------

CREATE TYPE app_role_enum AS ENUM (
    'admin',
    'bia_assessor',
    'approver_process_owner',
    'approver_top_management',
    'crisis_team_member',
    'viewer'
);

CREATE TABLE application_users (
    user_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    organization_id UUID NOT NULL REFERENCES organizations(organization_id) ON DELETE CASCADE,
    full_name VARCHAR(255) NOT NULL,
    email VARCHAR(255) NOT NULL UNIQUE,
    role app_role_enum NOT NULL DEFAULT 'viewer',
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_application_users_org ON application_users(organization_id);

-- ----------------------------------------------------------------------------
-- 8. MULTI-TIER SIGN-OFF WORKFLOW (FR14: Management Review & Digital Sign-Off)
-- ----------------------------------------------------------------------------
-- Generic across entity types so it covers BIA assessments, BC plans, and
-- recovery strategies without a separate approvals table per entity.

CREATE TYPE approval_entity_enum AS ENUM ('bia_assessment', 'bc_plan', 'recovery_strategy', 'crisis_management_plan');
CREATE TYPE approval_decision_enum AS ENUM ('pending', 'approved', 'rejected', 'returned_for_revision');

CREATE TABLE sign_off_approvals (
    approval_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    entity_type approval_entity_enum NOT NULL,
    entity_id UUID NOT NULL,          -- References bia_assessments.bia_id / bc_plans.plan_id / recovery_strategies.strategy_id, enforced at application layer (no single-parent FK across polymorphic types)
    sequence_order INT NOT NULL,      -- 1 = process owner tier, 2 = top management tier, etc.
    required_role app_role_enum NOT NULL,
    approver_user_id UUID REFERENCES application_users(user_id),
    decision approval_decision_enum NOT NULL DEFAULT 'pending',
    decision_date TIMESTAMPTZ,
    comments TEXT,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(entity_type, entity_id, sequence_order)
);

CREATE INDEX idx_sign_off_entity ON sign_off_approvals(entity_type, entity_id);

-- ----------------------------------------------------------------------------
-- 9. DOCUMENT REVIEW & MAINTENANCE SCHEDULER (FR15: Version Control & Maintenance Scheduler)
-- ----------------------------------------------------------------------------

CREATE TYPE review_trigger_enum AS ENUM ('periodic', 'event_driven');

CREATE TABLE document_review_schedule (
    schedule_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    entity_type approval_entity_enum NOT NULL,
    entity_id UUID NOT NULL,
    review_frequency_months INT DEFAULT 12,
    last_reviewed_date DATE,
    next_review_date DATE NOT NULL,
    review_trigger_type review_trigger_enum NOT NULL DEFAULT 'periodic',
    trigger_event_description TEXT, -- populated when review_trigger_type = 'event_driven' (e.g. major incident, org restructure)
    notes TEXT,
    UNIQUE(entity_type, entity_id)
);

CREATE INDEX idx_review_schedule_next_date ON document_review_schedule(next_review_date);

-- ----------------------------------------------------------------------------
-- 10. LIGHTWEIGHT VERSION HISTORY (FR15: full document version histories)
-- ----------------------------------------------------------------------------
-- Snapshot table rather than reusing audit_logs, so full historical document
-- content can be retrieved/exported without parsing generic change-diff JSON.

CREATE TABLE document_versions (
    version_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    entity_type approval_entity_enum NOT NULL,
    entity_id UUID NOT NULL,
    version_label VARCHAR(20) NOT NULL,   -- e.g. '1.0', '1.1', '2.0'
    snapshot_json JSONB NOT NULL,         -- full serialized document content at time of versioning
    change_summary TEXT,
    created_by_user_id UUID REFERENCES application_users(user_id),
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(entity_type, entity_id, version_label)
);

CREATE INDEX idx_document_versions_entity ON document_versions(entity_type, entity_id);
