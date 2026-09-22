-- ============================================================================
-- Enterprise Business Continuity & Disaster Recovery Webtool - Relational Database Schema
-- Standard Alignment: ISO 22301:2019, ISO TS 22317, BCI Good Practice Guidelines (GPG 7.0), NIST SP 800-34 Rev 1
-- Dialect: PostgreSQL 14+
-- Source: Peter van Walsem, database proposal (2026-09-22)
-- ============================================================================

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ============================================================================
-- 1. ORGANIZATIONAL HIERARCHY & RESOURCE CATALOG
-- ============================================================================

CREATE TYPE scope_level_enum AS ENUM ('product_service', 'process', 'activity');
CREATE TYPE resource_type_enum AS ENUM ('personnel', 'technology', 'facility', 'equipment', 'supplier');
CREATE TYPE strategy_category_enum AS ENUM ('active_active', 'hot_standby', 'warm_standby', 'cold_site', 'work_from_home', 'manual_workaround');

CREATE TABLE organizations (
    organization_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name VARCHAR(255) NOT NULL,
    industry VARCHAR(100),
    bcms_scope_description TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE business_units (
    unit_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    organization_id UUID NOT NULL REFERENCES organizations(organization_id) ON DELETE CASCADE,
    name VARCHAR(255) NOT NULL,
    code VARCHAR(50),
    head_of_unit VARCHAR(255),
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE products_services (
    product_service_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    organization_id UUID NOT NULL REFERENCES organizations(organization_id) ON DELETE CASCADE,
    name VARCHAR(255) NOT NULL,
    description TEXT,
    priority_ranking INT,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE business_processes (
    process_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    unit_id UUID NOT NULL REFERENCES business_units(unit_id) ON DELETE CASCADE,
    product_service_id UUID REFERENCES products_services(product_service_id) ON DELETE SET NULL,
    name VARCHAR(255) NOT NULL,
    process_owner VARCHAR(255) NOT NULL,
    is_outsourced BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE activities (
    activity_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    process_id UUID NOT NULL REFERENCES business_processes(process_id) ON DELETE CASCADE,
    parent_activity_id UUID REFERENCES activities(activity_id) ON DELETE CASCADE, -- Hierarchical parent-child support
    name VARCHAR(255) NOT NULL,
    description TEXT,
    activity_owner VARCHAR(255) NOT NULL,
    is_prioritised BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE resources (
    resource_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    organization_id UUID NOT NULL REFERENCES organizations(organization_id) ON DELETE CASCADE,
    resource_type resource_type_enum NOT NULL,
    name VARCHAR(255) NOT NULL,
    description TEXT,
    location VARCHAR(255),
    is_single_point_of_failure BOOLEAN DEFAULT FALSE,
    contact_details JSONB, -- Phone, email, emergency contact details
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE activity_resource_dependencies (
    dependency_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    activity_id UUID NOT NULL REFERENCES activities(activity_id) ON DELETE CASCADE,
    resource_id UUID NOT NULL REFERENCES resources(resource_id) ON DELETE CASCADE,
    dependency_type VARCHAR(100), -- Primary vs Secondary/Backup
    minimum_quantity_required INT DEFAULT 1,
    notes TEXT,
    UNIQUE(activity_id, resource_id)
);

-- ============================================================================
-- 2. BUSINESS IMPACT ANALYSIS (BIA) & PARAMETER ENGINE
-- ============================================================================

CREATE TYPE impact_category_enum AS ENUM ('financial', 'operational', 'reputational', 'legal_regulatory', 'health_safety');
CREATE TYPE severity_level_enum AS ENUM ('minimal', 'minor', 'moderate', 'major', 'severe', 'catastrophic');

CREATE TABLE impact_categories (
    category_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    organization_id UUID NOT NULL REFERENCES organizations(organization_id) ON DELETE CASCADE,
    category_type impact_category_enum NOT NULL,
    name VARCHAR(255) NOT NULL,
    description TEXT
);

CREATE TABLE impact_thresholds (
    threshold_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    category_id UUID NOT NULL REFERENCES impact_categories(category_id) ON DELETE CASCADE,
    severity severity_level_enum NOT NULL,
    time_timeframe_hours INT NOT NULL, -- Impact timeframe (e.g., 4h, 24h, 72h)
    financial_cost_min NUMERIC(15, 2),
    financial_cost_max NUMERIC(15, 2),
    qualitative_criteria TEXT NOT NULL
);

CREATE TABLE bia_assessments (
    bia_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    activity_id UUID NOT NULL REFERENCES activities(activity_id) ON DELETE CASCADE,
    assessor_name VARCHAR(255) NOT NULL,
    assessment_date DATE NOT NULL,

    -- Target Recovery Parameters
    mtpd_hours INT NOT NULL, -- Maximum Tolerable Period of Disruption (MTPD / MTD)
    rto_hours INT NOT NULL,  -- Recovery Time Objective (RTO < MTPD enforced by constraint)
    rpo_hours INT,           -- Recovery Point Objective (RPO / Maximum Data Loss)
    mbco_percentage NUMERIC(5,2) DEFAULT 100.00, -- Minimum Business Continuity Objective (% capacity)

    status VARCHAR(50) DEFAULT 'Draft', -- Draft, Pending Review, Approved
    approved_by VARCHAR(255),
    approval_date DATE,
    CONSTRAINT chk_rto_less_than_mtpd CHECK (rto_hours < mtpd_hours)
);

CREATE TABLE gap_analyses (
    gap_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    bia_id UUID NOT NULL REFERENCES bia_assessments(bia_id) ON DELETE CASCADE,
    current_recovery_capability_hours INT NOT NULL,
    target_rto_hours INT NOT NULL,
    gap_hours INT GENERATED ALWAYS AS (current_recovery_capability_hours - target_rto_hours) STORED,
    identified_spof TEXT,
    risk_summary TEXT NOT NULL
);

CREATE TABLE recovery_strategies (
    strategy_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    bia_id UUID NOT NULL REFERENCES bia_assessments(bia_id) ON DELETE CASCADE,
    category strategy_category_enum NOT NULL,
    strategy_name VARCHAR(255) NOT NULL,
    description TEXT NOT NULL,
    estimated_implementation_cost NUMERIC(15, 2),
    is_selected_option BOOLEAN DEFAULT FALSE,
    prerequisites TEXT
);

-- ============================================================================
-- 3. BUSINESS CONTINUITY PLANS (BCP) & RETURN TO BAU
-- ============================================================================

CREATE TYPE plan_tier_enum AS ENUM ('strategic', 'tactical', 'operational');

CREATE TABLE bc_plans (
    plan_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    organization_id UUID NOT NULL REFERENCES organizations(organization_id) ON DELETE CASCADE,
    plan_tier plan_tier_enum NOT NULL,
    plan_title VARCHAR(255) NOT NULL,
    version VARCHAR(20) DEFAULT '1.0',
    plan_owner VARCHAR(255) NOT NULL,
    invocation_criteria TEXT NOT NULL,
    alternate_facility_details TEXT,
    status VARCHAR(50) DEFAULT 'Draft',
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE bcp_action_steps (
    step_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    plan_id UUID NOT NULL REFERENCES bc_plans(plan_id) ON DELETE CASCADE,
    step_number INT NOT NULL,
    responsible_role VARCHAR(255) NOT NULL,
    action_title VARCHAR(255) NOT NULL,
    detailed_instructions TEXT NOT NULL,
    timeframe_offset_minutes INT, -- Minutes from plan invocation (T+0, T+30, T+60)
    CHECK (step_number > 0)
);

CREATE TABLE bau_return_procedures (
    bau_procedure_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    plan_id UUID NOT NULL REFERENCES bc_plans(plan_id) ON DELETE CASCADE,
    phase_number INT NOT NULL,
    title VARCHAR(255) NOT NULL,
    restoration_type VARCHAR(100) NOT NULL, -- e.g., Primary Resource Restoration, Transition to New Normal
    validation_criteria TEXT NOT NULL,
    action_steps TEXT NOT NULL
);

-- ============================================================================
-- 4. CRISIS MANAGEMENT PLAN (CMP) & COMMUNICATIONS
-- ============================================================================

CREATE TABLE cmt_roles (
    role_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    organization_id UUID NOT NULL REFERENCES organizations(organization_id) ON DELETE CASCADE,
    role_name VARCHAR(255) NOT NULL, -- e.g., Crisis Management Team Leader (CMTL), Legal Counsel, IT/Security Head, Spokesperson
    primary_assignee_name VARCHAR(255) NOT NULL,
    primary_assignee_phone VARCHAR(50) NOT NULL,
    primary_assignee_email VARCHAR(255) NOT NULL,
    alternate_assignee_name VARCHAR(255),
    alternate_assignee_phone VARCHAR(50),
    key_responsibilities TEXT NOT NULL
);

CREATE TABLE escalation_triggers (
    trigger_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    organization_id UUID NOT NULL REFERENCES organizations(organization_id) ON DELETE CASCADE,
    severity_level severity_level_enum NOT NULL,
    incident_condition TEXT NOT NULL,
    notification_timeframe_minutes INT NOT NULL,
    required_action TEXT NOT NULL
);

CREATE TABLE stakeholder_contact_matrices (
    contact_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    organization_id UUID NOT NULL REFERENCES organizations(organization_id) ON DELETE CASCADE,
    stakeholder_group VARCHAR(100) NOT NULL, -- Internal Personnel, Executive Leadership, Media, Regulators, Key Vendors
    contact_person_or_entity VARCHAR(255) NOT NULL,
    primary_channel VARCHAR(50) NOT NULL, -- Phone, SMS, Email, Press Release, Social Media
    backup_channel VARCHAR(50),
    notification_priority INT DEFAULT 1
);

CREATE TABLE message_bank (
    message_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    organization_id UUID NOT NULL REFERENCES organizations(organization_id) ON DELETE CASCADE,
    scenario_type VARCHAR(100) NOT NULL, -- Power Outage, Cyberattack/DDoS, Public Transit Disruption, Data Breach
    target_audience VARCHAR(100) NOT NULL,
    holding_statement_template TEXT NOT NULL,
    pre_approved_by_legal BOOLEAN DEFAULT FALSE,
    dispatch_channels TEXT[] -- Array of channels e.g. {'SMS', 'Email', 'Social Media'}
);

-- ============================================================================
-- 5. TEST & EXERCISE PROGRAMME (VALIDATION & CAPA)
-- ============================================================================

CREATE TYPE exercise_category_enum AS ENUM ('discussion_based', 'scenario_tabletop', 'simulation', 'live', 'functional_test');

CREATE TABLE exercise_programmes (
    programme_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    organization_id UUID NOT NULL REFERENCES organizations(organization_id) ON DELETE CASCADE,
    title VARCHAR(255) NOT NULL,
    annual_schedule_year INT NOT NULL,
    objectives TEXT NOT NULL,
    approved_budget NUMERIC(15, 2)
);

CREATE TABLE exercises (
    exercise_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    programme_id UUID NOT NULL REFERENCES exercise_programmes(programme_id) ON DELETE CASCADE,
    category exercise_category_enum NOT NULL,
    title VARCHAR(255) NOT NULL,
    planned_date DATE NOT NULL,
    lead_facilitator VARCHAR(255) NOT NULL,
    scenario_description TEXT NOT NULL,
    status VARCHAR(50) DEFAULT 'Scheduled' -- Scheduled, In Progress, Completed, Cancelled
);

CREATE TABLE scenario_injects (
    inject_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    exercise_id UUID NOT NULL REFERENCES exercises(exercise_id) ON DELETE CASCADE,
    sequence_number INT NOT NULL,
    time_offset_minutes INT NOT NULL, -- Execution storyboard timestamp
    inject_title VARCHAR(255) NOT NULL,
    inject_content TEXT NOT NULL,
    delivery_method VARCHAR(50) NOT NULL, -- Facilitator Announcement, Simulated Email, Phone Call
    expected_team_action TEXT NOT NULL
);

CREATE TABLE exercise_debriefs (
    debrief_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    exercise_id UUID UNIQUE NOT NULL REFERENCES exercises(exercise_id) ON DELETE CASCADE,
    hot_debrief_summary TEXT NOT NULL,
    strengths_observed TEXT,
    weaknesses_observed TEXT,
    opportunities_for_improvement TEXT,
    identified_threats_risks TEXT,
    overall_rating VARCHAR(50) -- Unsatisfactory, Satisfactory, Excellent
);

CREATE TABLE capa_action_items (
    action_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    debrief_id UUID NOT NULL REFERENCES exercise_debriefs(debrief_id) ON DELETE CASCADE,
    gap_description TEXT NOT NULL,
    corrective_action_required TEXT NOT NULL,
    assigned_owner VARCHAR(255) NOT NULL,
    due_date DATE NOT NULL,
    status VARCHAR(50) DEFAULT 'Open', -- Open, In Progress, Completed, Verified
    completion_date DATE
);

-- ============================================================================
-- 6. AUDIT LOGGING & DOCUMENT GOVERNANCE
-- ============================================================================

CREATE TABLE audit_logs (
    log_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    organization_id UUID REFERENCES organizations(organization_id) ON DELETE CASCADE,
    user_id VARCHAR(255) NOT NULL,
    action VARCHAR(100) NOT NULL, -- CREATE, UPDATE, DELETE, APPROVE, INVOKE
    entity_affected VARCHAR(100) NOT NULL,
    entity_id UUID NOT NULL,
    changes_json JSONB,
    timestamp TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

-- ============================================================================
-- INDEXES FOR PERFORMANCE OPTIMIZATION
-- ============================================================================

CREATE INDEX idx_business_units_org ON business_units(organization_id);
CREATE INDEX idx_business_processes_unit ON business_processes(unit_id);
CREATE INDEX idx_activities_process ON activities(process_id);
CREATE INDEX idx_activity_deps_activity ON activity_resource_dependencies(activity_id);
CREATE INDEX idx_bia_assessments_activity ON bia_assessments(activity_id);
CREATE INDEX idx_bcp_action_steps_plan ON bcp_action_steps(plan_id);
CREATE INDEX idx_scenario_injects_exercise ON scenario_injects(exercise_id);
CREATE INDEX idx_capa_debrief ON capa_action_items(debrief_id);
