#!/usr/bin/env python3
"""BCM Continuity Planner — Model Context Protocol (MCP) Server.

FastMCP server exposing the Phase 1 BIA Engine as MCP tools over stdio,
consistent with the pattern used across Peter van Walsem's other IT risk
tooling repos (pqc-cbom-risk-auditor, ai-risk-auditor, vendor-soc-isae-auditor,
stride-threat-modeler).

Every tool opens a short-lived Postgres connection via bcm_planner.db,
delegates to bcm_planner.bia_engine, and returns the resulting row(s) or a
clear error. RBAC and audit logging are enforced inside bia_engine, not here.
"""

from __future__ import annotations

import datetime
from typing import Any, Optional

from fastmcp import FastMCP

from bcm_planner import (
    bcp_generator,
    bia_engine,
    crisis_communications,
    crisis_management,
    db,
    exercise_debrief,
    exercise_planner,
    governance,
    scenario_injects,
)

mcp = FastMCP(
    name="bcm-continuity-planner-mcp",
    version="0.5.0",
    instructions=(
        "BIA Engine (Phase 1) + Plan Generators (Phase 2) + Crisis Management "
        "(Phase 3) + Exercise & Test Planner (Phase 4) + Governance & "
        "Lifecycle (Phase 5) for the BCM Continuity Planner: scope/hierarchy "
        "CRUD, impact matrix configuration, MTPD/RTO/RPO/MBCO capture with "
        "enforced RTO<MTPD, gap analysis with single-point-of-failure "
        "detection, recovery strategy selection, BCP template builder + "
        "workflow generator (bc_plans/bcp_action_steps, including rule-based "
        "auto-generation from a BIA + recovery strategy), Return-to-BAU "
        "procedures, Crisis Management Team (CMT) role + escalation trigger "
        "CRUD with a severity-based escalation path helper, Crisis "
        "Communication stakeholder contact matrix + message bank CRUD with a "
        "rule-based (draft-only, never legally pre-approved) holding "
        "statement generator, exercise programme/exercise CRUD with a "
        "rule-based disruption scenario template helper, scenario inject/"
        "storyboard CRUD with timeline validation and rule-based inject "
        "generation, exercise debrief + CAPA action item CRUD with an "
        "overdue-CAPA tracking helper, a multi-tier (process owner -> top "
        "management) sign-off workflow state machine for BIA/BCP/recovery "
        "strategy/exercise debrief entities, and a document review-cycle "
        "scheduler + version/maintenance log spanning every governed entity "
        "type. All write tools require a valid application_users.user_id "
        "with a permitted role."
    ),
)


def _err(exc: Exception) -> dict[str, Any]:
    return {"error": type(exc).__name__, "message": str(exc)}


# ---------------------------------------------------------------------------
# Scope / hierarchy
# ---------------------------------------------------------------------------


@mcp.tool()
def create_organization(
    user_id: str, name: str, bcms_scope_description: str, industry: Optional[str] = None
) -> dict[str, Any]:
    """Creates a new organization (top of the BIA scope hierarchy)."""
    try:
        with db.get_connection() as conn:
            return bia_engine.create_organization(conn, user_id, name, bcms_scope_description, industry)
    except bia_engine.BCMPlannerError as exc:
        return _err(exc)


@mcp.tool()
def get_organization(organization_id: str) -> dict[str, Any]:
    """Reads a single organization by ID."""
    try:
        with db.get_connection() as conn:
            return bia_engine.get_organization(conn, organization_id)
    except bia_engine.BCMPlannerError as exc:
        return _err(exc)


@mcp.tool()
def create_business_unit(
    user_id: str, organization_id: str, name: str,
    code: Optional[str] = None, head_of_unit: Optional[str] = None,
) -> dict[str, Any]:
    """Creates a business unit under an organization."""
    try:
        with db.get_connection() as conn:
            return bia_engine.create_business_unit(conn, user_id, organization_id, name, code, head_of_unit)
    except bia_engine.BCMPlannerError as exc:
        return _err(exc)


@mcp.tool()
def create_product_service(
    user_id: str, organization_id: str, name: str,
    description: Optional[str] = None, priority_ranking: Optional[int] = None,
) -> dict[str, Any]:
    """Creates a product/service entry under an organization."""
    try:
        with db.get_connection() as conn:
            return bia_engine.create_product_service(conn, user_id, organization_id, name, description, priority_ranking)
    except bia_engine.BCMPlannerError as exc:
        return _err(exc)


@mcp.tool()
def update_product_service(user_id: str, product_service_id: str, fields: dict[str, Any]) -> dict[str, Any]:
    """Updates mutable fields on a product/service. fields may include:
    name, description, priority_ranking, worst_case_scenario.
    """
    try:
        with db.get_connection() as conn:
            return bia_engine.update_product_service(conn, user_id, product_service_id, **fields)
    except bia_engine.BCMPlannerError as exc:
        return _err(exc)


@mcp.tool()
def create_business_process(
    user_id: str, unit_id: str, name: str, process_owner: str,
    product_service_id: Optional[str] = None, is_outsourced: bool = False,
) -> dict[str, Any]:
    """Creates a business process under a business unit, optionally linked to a product/service."""
    try:
        with db.get_connection() as conn:
            return bia_engine.create_business_process(conn, user_id, unit_id, name, process_owner, product_service_id, is_outsourced)
    except bia_engine.BCMPlannerError as exc:
        return _err(exc)


@mcp.tool()
def update_business_process(user_id: str, process_id: str, fields: dict[str, Any]) -> dict[str, Any]:
    """Updates mutable fields on a business process. fields may include:
    name, process_owner, product_service_id, is_outsourced, worst_case_scenario.
    """
    try:
        with db.get_connection() as conn:
            return bia_engine.update_business_process(conn, user_id, process_id, **fields)
    except bia_engine.BCMPlannerError as exc:
        return _err(exc)


@mcp.tool()
def create_activity(
    user_id: str, process_id: str, name: str, activity_owner: str,
    parent_activity_id: Optional[str] = None, description: Optional[str] = None,
    is_prioritised: bool = False,
) -> dict[str, Any]:
    """Creates an activity under a process, optionally as a child of another activity."""
    try:
        with db.get_connection() as conn:
            return bia_engine.create_activity(conn, user_id, process_id, name, activity_owner, parent_activity_id, description, is_prioritised)
    except bia_engine.BCMPlannerError as exc:
        return _err(exc)


@mcp.tool()
def update_activity(user_id: str, activity_id: str, fields: dict[str, Any]) -> dict[str, Any]:
    """Updates mutable fields on an activity. fields may include: name,
    description, activity_owner, is_prioritised, worst_case_scenario.
    """
    try:
        with db.get_connection() as conn:
            return bia_engine.update_activity(conn, user_id, activity_id, **fields)
    except bia_engine.BCMPlannerError as exc:
        return _err(exc)


@mcp.tool()
def get_activity_tree(process_id: str) -> list[dict[str, Any]]:
    """Returns the nested parent/child activity tree for a process."""
    with db.get_connection() as conn:
        return bia_engine.get_activity_tree(conn, process_id)


@mcp.tool()
def create_resource(
    user_id: str, organization_id: str, resource_type: str, name: str,
    description: Optional[str] = None, location: Optional[str] = None,
    is_single_point_of_failure: bool = False, contact_details: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Creates a resource (personnel/technology/facility/equipment/supplier)."""
    try:
        with db.get_connection() as conn:
            return bia_engine.create_resource(
                conn, user_id, organization_id, resource_type, name, description,
                location, is_single_point_of_failure, contact_details,
            )
    except bia_engine.BCMPlannerError as exc:
        return _err(exc)


@mcp.tool()
def update_resource(user_id: str, resource_id: str, fields: dict[str, Any]) -> dict[str, Any]:
    """Updates mutable fields on a resource. fields may include: name,
    description, location, is_single_point_of_failure, resource_type.
    """
    try:
        with db.get_connection() as conn:
            return bia_engine.update_resource(conn, user_id, resource_id, **fields)
    except bia_engine.BCMPlannerError as exc:
        return _err(exc)


@mcp.tool()
def create_resource_recovery_measure(
    user_id: str, resource_id: str, measure_type: str, description: str,
    recovery_time_hours: Optional[int] = None, status: str = "not_started",
    owner: Optional[str] = None, estimated_cost: Optional[float] = None,
) -> dict[str, Any]:
    """Creates a recovery measure (backup/redundancy mitigation) attached
    directly to a resource. status must be one of: not_started, planned,
    in_place. Distinct from recovery_strategies (which hang off a BIA/activity).
    """
    try:
        with db.get_connection() as conn:
            return bia_engine.create_resource_recovery_measure(
                conn, user_id, resource_id, measure_type, description,
                recovery_time_hours, status, owner, estimated_cost,
            )
    except bia_engine.BCMPlannerError as exc:
        return _err(exc)


@mcp.tool()
def update_resource_recovery_measure(user_id: str, measure_id: str, fields: dict[str, Any]) -> dict[str, Any]:
    """Updates mutable fields on a resource recovery measure. fields may
    include: measure_type, description, recovery_time_hours, status, owner,
    estimated_cost.
    """
    try:
        with db.get_connection() as conn:
            return bia_engine.update_resource_recovery_measure(conn, user_id, measure_id, **fields)
    except bia_engine.BCMPlannerError as exc:
        return _err(exc)


@mcp.tool()
def list_resource_recovery_measures(resource_id: str) -> list[dict[str, Any]]:
    """Lists all recovery measures for a resource."""
    with db.get_connection() as conn:
        return bia_engine.list_resource_recovery_measures(conn, resource_id)


@mcp.tool()
def upsert_impact_matrix_entry(
    user_id: str, scope_type: str, scope_id: str, category: str,
    timeframe_hours: int, severity: str, notes: Optional[str] = None,
) -> dict[str, Any]:
    """Creates or updates one cell of the impact matrix grid.

    scope_type: one of product_service, business_process, activity.
    category: one of financial, reputation_customer, operational, compliance.
    timeframe_hours: one of 1, 4, 8, 24 (1 day), 72 (3 days), 168 (1 week).
    severity: one of low, medium, high, critical.
    Idempotent: calling again for the same (scope_type, scope_id, category,
    timeframe_hours) updates the existing cell rather than erroring.
    """
    try:
        with db.get_connection() as conn:
            return bia_engine.upsert_impact_matrix_entry(
                conn, user_id, scope_type, scope_id, category, timeframe_hours, severity, notes,
            )
    except bia_engine.BCMPlannerError as exc:
        return _err(exc)


@mcp.tool()
def list_impact_matrix_entries(scope_type: str, scope_id: str) -> list[dict[str, Any]]:
    """Lists all impact matrix entries recorded for a given scope (product/service, process, or activity)."""
    with db.get_connection() as conn:
        return bia_engine.list_impact_matrix_entries(conn, scope_type, scope_id)


@mcp.tool()
def link_activity_resource_dependency(
    user_id: str, activity_id: str, resource_id: str,
    dependency_type: Optional[str] = None, minimum_quantity_required: int = 1,
    notes: Optional[str] = None,
) -> dict[str, Any]:
    """Links a resource as a dependency of an activity."""
    try:
        with db.get_connection() as conn:
            return bia_engine.link_activity_resource_dependency(
                conn, user_id, activity_id, resource_id, dependency_type, minimum_quantity_required, notes
            )
    except bia_engine.BCMPlannerError as exc:
        return _err(exc)


@mcp.tool()
def get_activity_resource_dependencies(activity_id: str) -> list[dict[str, Any]]:
    """Lists all resource dependencies for an activity, joined with resource details."""
    with db.get_connection() as conn:
        return bia_engine.get_activity_resource_dependencies(conn, activity_id)


# ---------------------------------------------------------------------------
# Impact matrix configuration
# ---------------------------------------------------------------------------


@mcp.tool()
def create_impact_category(
    user_id: str, organization_id: str, category_type: str, name: str, description: Optional[str] = None
) -> dict[str, Any]:
    """Creates an impact category (financial/operational/reputational/legal_regulatory/health_safety)."""
    try:
        with db.get_connection() as conn:
            return bia_engine.create_impact_category(conn, user_id, organization_id, category_type, name, description)
    except bia_engine.BCMPlannerError as exc:
        return _err(exc)


@mcp.tool()
def create_impact_threshold(
    user_id: str, category_id: str, severity: str, time_timeframe_hours: int,
    qualitative_criteria: str, financial_cost_min: Optional[float] = None,
    financial_cost_max: Optional[float] = None,
) -> dict[str, Any]:
    """Creates a severity threshold row for an impact category."""
    try:
        with db.get_connection() as conn:
            return bia_engine.create_impact_threshold(
                conn, user_id, category_id, severity, time_timeframe_hours,
                qualitative_criteria, financial_cost_min, financial_cost_max,
            )
    except bia_engine.BCMPlannerError as exc:
        return _err(exc)


# ---------------------------------------------------------------------------
# MTPD / RTO / RPO / MBCO
# ---------------------------------------------------------------------------


@mcp.tool()
def create_bia_assessment(
    user_id: str, activity_id: str, assessor_name: str, assessment_date: str,
    mtpd_hours: int, rto_hours: int, rpo_hours: Optional[int] = None,
    mbco_percentage: float = 100.00,
) -> dict[str, Any]:
    """Creates a BIA assessment (MTPD/RTO/RPO/MBCO) for an activity.

    RTO must be strictly less than MTPD — violations return a clear error
    (RTOConstraintViolation) instead of a raw database traceback.
    `assessment_date` is an ISO date string (YYYY-MM-DD).
    """
    try:
        parsed_date = datetime.date.fromisoformat(assessment_date)
        with db.get_connection() as conn:
            return bia_engine.create_bia_assessment(
                conn, user_id, activity_id, assessor_name, parsed_date,
                mtpd_hours, rto_hours, rpo_hours, mbco_percentage,
            )
    except bia_engine.BCMPlannerError as exc:
        return _err(exc)


@mcp.tool()
def update_bia_assessment(user_id: str, bia_id: str, fields: dict[str, Any]) -> dict[str, Any]:
    """Updates mutable fields on an existing BIA assessment.

    `fields` may include: assessor_name, assessment_date (ISO string),
    mtpd_hours, rto_hours, rpo_hours, mbco_percentage, status, approved_by,
    approval_date (ISO string). RTO<MTPD is re-validated if either changes.
    """
    try:
        parsed = dict(fields)
        if "assessment_date" in parsed and isinstance(parsed["assessment_date"], str):
            parsed["assessment_date"] = datetime.date.fromisoformat(parsed["assessment_date"])
        if "approval_date" in parsed and isinstance(parsed["approval_date"], str):
            parsed["approval_date"] = datetime.date.fromisoformat(parsed["approval_date"])
        with db.get_connection() as conn:
            return bia_engine.update_bia_assessment(conn, user_id, bia_id, **parsed)
    except bia_engine.BCMPlannerError as exc:
        return _err(exc)


@mcp.tool()
def get_bia_assessment(bia_id: str) -> dict[str, Any]:
    """Reads a single BIA assessment by ID."""
    try:
        with db.get_connection() as conn:
            return bia_engine.get_bia_assessment(conn, bia_id)
    except bia_engine.BCMPlannerError as exc:
        return _err(exc)


@mcp.tool()
def list_bia_assessments_by_activity(activity_id: str) -> list[dict[str, Any]]:
    """Lists all BIA assessments for a given activity, most recent first."""
    with db.get_connection() as conn:
        return bia_engine.list_bia_assessments_by_activity(conn, activity_id)


# ---------------------------------------------------------------------------
# Gap analysis
# ---------------------------------------------------------------------------


@mcp.tool()
def create_gap_analysis(
    user_id: str, bia_id: str, current_recovery_capability_hours: int,
    target_rto_hours: int, risk_summary: str, identified_spof: Optional[str] = None,
) -> dict[str, Any]:
    """Creates a gap analysis for a BIA assessment. gap_hours is computed by
    the database (GENERATED ALWAYS AS STORED) and returned, never written.
    """
    try:
        with db.get_connection() as conn:
            return bia_engine.create_gap_analysis(
                conn, user_id, bia_id, current_recovery_capability_hours,
                target_rto_hours, risk_summary, identified_spof,
            )
    except bia_engine.BCMPlannerError as exc:
        return _err(exc)


@mcp.tool()
def list_gap_analyses_by_bia(bia_id: str) -> list[dict[str, Any]]:
    """Lists all gap analyses for a BIA assessment."""
    with db.get_connection() as conn:
        return bia_engine.list_gap_analyses_by_bia(conn, bia_id)


@mcp.tool()
def detect_single_points_of_failure(activity_id: str) -> list[dict[str, Any]]:
    """Flags resource dependencies of an activity where the resource is
    marked as a single point of failure (resources.is_single_point_of_failure).
    """
    with db.get_connection() as conn:
        return bia_engine.detect_single_points_of_failure(conn, activity_id)


# ---------------------------------------------------------------------------
# Recovery strategy selection
# ---------------------------------------------------------------------------


@mcp.tool()
def create_recovery_strategy(
    user_id: str, bia_id: str, category: str, strategy_name: str, description: str,
    estimated_implementation_cost: Optional[float] = None, prerequisites: Optional[str] = None,
    is_selected_option: bool = False,
) -> dict[str, Any]:
    """Creates a recovery strategy option for a BIA assessment.

    If is_selected_option=True, any previously-selected strategy for the
    same bia_id is un-marked in the same transaction (only one selected
    strategy per bia_id, enforced in application logic).
    """
    try:
        with db.get_connection() as conn:
            return bia_engine.create_recovery_strategy(
                conn, user_id, bia_id, category, strategy_name, description,
                estimated_implementation_cost, prerequisites, is_selected_option,
            )
    except bia_engine.BCMPlannerError as exc:
        return _err(exc)


@mcp.tool()
def select_recovery_strategy(user_id: str, bia_id: str, strategy_id: str) -> dict[str, Any]:
    """Marks a recovery strategy as selected for a BIA assessment,
    un-marking any previously-selected strategy for that same bia_id.
    """
    try:
        with db.get_connection() as conn:
            return bia_engine.select_recovery_strategy(conn, user_id, bia_id, strategy_id)
    except bia_engine.BCMPlannerError as exc:
        return _err(exc)


@mcp.tool()
def list_recovery_strategies_by_bia(bia_id: str) -> list[dict[str, Any]]:
    """Lists all recovery strategy options for a BIA assessment."""
    with db.get_connection() as conn:
        return bia_engine.list_recovery_strategies_by_bia(conn, bia_id)


# ---------------------------------------------------------------------------
# Phase 2 — BCP Template Builder & Workflow Generator (FR7)
# ---------------------------------------------------------------------------


@mcp.tool()
def create_bc_plan(
    user_id: str, organization_id: str, plan_tier: str, plan_title: str,
    plan_owner: str, invocation_criteria: str, version: str = "1.0",
    alternate_facility_details: Optional[str] = None, status: str = "Draft",
) -> dict[str, Any]:
    """Creates a Business Continuity Plan (bc_plans). plan_tier must be one
    of 'strategic', 'tactical', 'operational'.
    """
    try:
        with db.get_connection() as conn:
            return bcp_generator.create_bc_plan(
                conn, user_id, organization_id, plan_tier, plan_title, plan_owner,
                invocation_criteria, version, alternate_facility_details, status,
            )
    except bcp_generator.BCMPlannerError as exc:
        return _err(exc)


@mcp.tool()
def get_bc_plan(plan_id: str) -> dict[str, Any]:
    """Reads a single BC plan by ID."""
    try:
        with db.get_connection() as conn:
            return bcp_generator.get_bc_plan(conn, plan_id)
    except bcp_generator.BCMPlannerError as exc:
        return _err(exc)


@mcp.tool()
def list_bc_plans_by_organization(organization_id: str) -> list[dict[str, Any]]:
    """Lists all BC plans for an organization, most recently created first."""
    with db.get_connection() as conn:
        return bcp_generator.list_bc_plans_by_organization(conn, organization_id)


@mcp.tool()
def update_bc_plan(user_id: str, plan_id: str, fields: dict[str, Any]) -> dict[str, Any]:
    """Updates mutable fields on an existing BC plan.

    `fields` may include: plan_tier, plan_title, version, plan_owner,
    invocation_criteria, alternate_facility_details, status.
    """
    try:
        with db.get_connection() as conn:
            return bcp_generator.update_bc_plan(conn, user_id, plan_id, **fields)
    except bcp_generator.BCMPlannerError as exc:
        return _err(exc)


@mcp.tool()
def create_bcp_action_step(
    user_id: str, plan_id: str, step_number: int, responsible_role: str,
    action_title: str, detailed_instructions: str,
    timeframe_offset_minutes: Optional[int] = None,
) -> dict[str, Any]:
    """Creates an action step under a BC plan. step_number must be > 0."""
    try:
        with db.get_connection() as conn:
            return bcp_generator.create_bcp_action_step(
                conn, user_id, plan_id, step_number, responsible_role,
                action_title, detailed_instructions, timeframe_offset_minutes,
            )
    except bcp_generator.BCMPlannerError as exc:
        return _err(exc)


@mcp.tool()
def list_bcp_action_steps_by_plan(plan_id: str) -> list[dict[str, Any]]:
    """Lists all action steps for a BC plan, ordered by step_number."""
    with db.get_connection() as conn:
        return bcp_generator.list_bcp_action_steps_by_plan(conn, plan_id)


@mcp.tool()
def update_bcp_action_step(user_id: str, step_id: str, fields: dict[str, Any]) -> dict[str, Any]:
    """Updates mutable fields on an existing BCP action step.

    `fields` may include: step_number, responsible_role, action_title,
    detailed_instructions, timeframe_offset_minutes.
    """
    try:
        with db.get_connection() as conn:
            return bcp_generator.update_bcp_action_step(conn, user_id, step_id, **fields)
    except bcp_generator.BCMPlannerError as exc:
        return _err(exc)


@mcp.tool()
def generate_bcp_draft_from_bia(
    user_id: str, bia_id: str, plan_tier: str = "operational",
    plan_owner: Optional[str] = None, recovery_strategy_id: Optional[str] = None,
) -> dict[str, Any]:
    """Auto-populates a draft BC plan + starter action steps from an
    existing BIA assessment, its activity's resource dependencies, and a
    selected (or explicitly given) recovery strategy.

    Rule-based generation, not AI-generated prose — see
    docs/bcp_generation_rules.md for the full mapping. Returns
    {"plan": <bc_plans row>, "action_steps": [<bcp_action_steps row>, ...]}.
    """
    try:
        with db.get_connection() as conn:
            return bcp_generator.generate_bcp_draft_from_bia(
                conn, user_id, bia_id, plan_tier, plan_owner, recovery_strategy_id
            )
    except bcp_generator.BCMPlannerError as exc:
        return _err(exc)


@mcp.tool()
def get_bc_plan_provenance(plan_id: str) -> Optional[dict[str, Any]]:
    """Reads back the auto-generation provenance record (source BIA/
    activity/recovery strategy) for a BC plan, if it was auto-generated via
    generate_bcp_draft_from_bia. Returns None for hand-authored plans.
    """
    with db.get_connection() as conn:
        return bcp_generator.get_bc_plan_provenance(conn, plan_id)


# ---------------------------------------------------------------------------
# Phase 2 — Return to BAU Module (FR8)
# ---------------------------------------------------------------------------


@mcp.tool()
def create_bau_return_procedure(
    user_id: str, plan_id: str, phase_number: int, title: str,
    restoration_type: str, validation_criteria: str, action_steps: str,
) -> dict[str, Any]:
    """Creates a Return-to-BAU procedure phase under a BC plan."""
    try:
        with db.get_connection() as conn:
            return bcp_generator.create_bau_return_procedure(
                conn, user_id, plan_id, phase_number, title, restoration_type,
                validation_criteria, action_steps,
            )
    except bcp_generator.BCMPlannerError as exc:
        return _err(exc)


@mcp.tool()
def list_bau_return_procedures_by_plan(plan_id: str) -> list[dict[str, Any]]:
    """Lists all Return-to-BAU procedure phases for a BC plan, ordered by phase_number."""
    with db.get_connection() as conn:
        return bcp_generator.list_bau_return_procedures_by_plan(conn, plan_id)


@mcp.tool()
def update_bau_return_procedure(
    user_id: str, bau_procedure_id: str, fields: dict[str, Any]
) -> dict[str, Any]:
    """Updates mutable fields on an existing Return-to-BAU procedure phase.

    `fields` may include: phase_number, title, restoration_type,
    validation_criteria, action_steps.
    """
    try:
        with db.get_connection() as conn:
            return bcp_generator.update_bau_return_procedure(conn, user_id, bau_procedure_id, **fields)
    except bcp_generator.BCMPlannerError as exc:
        return _err(exc)


@mcp.tool()
def generate_bau_return_phases(user_id: str, plan_id: str) -> list[dict[str, Any]]:
    """Auto-generates the standard 4-phase Return-to-BAU set for a BC plan:
    Verify primary resource restoration -> Parallel run / validation ->
    Cutover to primary -> Post-incident review handoff.

    Rule-based generation — see docs/bcp_generation_rules.md section 2.
    """
    try:
        with db.get_connection() as conn:
            return bcp_generator.generate_bau_return_phases(conn, user_id, plan_id)
    except bcp_generator.BCMPlannerError as exc:
        return _err(exc)


# ---------------------------------------------------------------------------
# Phase 3 — Crisis Management Team (CMT) & Escalation Mapping (FR9)
# ---------------------------------------------------------------------------


@mcp.tool()
def create_cmt_role(
    user_id: str, organization_id: str, role_name: str,
    primary_assignee_name: str, primary_assignee_phone: str, primary_assignee_email: str,
    key_responsibilities: str, alternate_assignee_name: Optional[str] = None,
    alternate_assignee_phone: Optional[str] = None,
) -> dict[str, Any]:
    """Creates a Crisis Management Team role definition (e.g. Crisis
    Management Team Leader, Legal Counsel, IT/Security Head, Spokesperson).
    """
    try:
        with db.get_connection() as conn:
            return crisis_management.create_cmt_role(
                conn, user_id, organization_id, role_name, primary_assignee_name,
                primary_assignee_phone, primary_assignee_email, key_responsibilities,
                alternate_assignee_name, alternate_assignee_phone,
            )
    except crisis_management.BCMPlannerError as exc:
        return _err(exc)


@mcp.tool()
def get_cmt_role(role_id: str) -> dict[str, Any]:
    """Reads a single CMT role by ID."""
    try:
        with db.get_connection() as conn:
            return crisis_management.get_cmt_role(conn, role_id)
    except crisis_management.BCMPlannerError as exc:
        return _err(exc)


@mcp.tool()
def list_cmt_roles_by_organization(organization_id: str) -> list[dict[str, Any]]:
    """Lists all CMT roles for an organization, ordered by role_name."""
    with db.get_connection() as conn:
        return crisis_management.list_cmt_roles_by_organization(conn, organization_id)


@mcp.tool()
def update_cmt_role(user_id: str, role_id: str, fields: dict[str, Any]) -> dict[str, Any]:
    """Updates mutable fields on an existing CMT role.

    `fields` may include: role_name, primary_assignee_name,
    primary_assignee_phone, primary_assignee_email, alternate_assignee_name,
    alternate_assignee_phone, key_responsibilities.
    """
    try:
        with db.get_connection() as conn:
            return crisis_management.update_cmt_role(conn, user_id, role_id, **fields)
    except crisis_management.BCMPlannerError as exc:
        return _err(exc)


@mcp.tool()
def create_escalation_trigger(
    user_id: str, organization_id: str, severity_level: str, incident_condition: str,
    notification_timeframe_minutes: int, required_action: str,
) -> dict[str, Any]:
    """Creates a severity-based escalation trigger. severity_level must be
    one of severity_level_enum's values (minimal/minor/moderate/major/
    severe/catastrophic).
    """
    try:
        with db.get_connection() as conn:
            return crisis_management.create_escalation_trigger(
                conn, user_id, organization_id, severity_level, incident_condition,
                notification_timeframe_minutes, required_action,
            )
    except crisis_management.BCMPlannerError as exc:
        return _err(exc)


@mcp.tool()
def get_escalation_trigger(trigger_id: str) -> dict[str, Any]:
    """Reads a single escalation trigger by ID."""
    try:
        with db.get_connection() as conn:
            return crisis_management.get_escalation_trigger(conn, trigger_id)
    except crisis_management.BCMPlannerError as exc:
        return _err(exc)


@mcp.tool()
def list_escalation_triggers_by_organization(organization_id: str) -> list[dict[str, Any]]:
    """Lists all escalation triggers for an organization, ordered by severity_level."""
    with db.get_connection() as conn:
        return crisis_management.list_escalation_triggers_by_organization(conn, organization_id)


@mcp.tool()
def update_escalation_trigger(user_id: str, trigger_id: str, fields: dict[str, Any]) -> dict[str, Any]:
    """Updates mutable fields on an existing escalation trigger.

    `fields` may include: severity_level, incident_condition,
    notification_timeframe_minutes, required_action.
    """
    try:
        with db.get_connection() as conn:
            return crisis_management.update_escalation_trigger(conn, user_id, trigger_id, **fields)
    except crisis_management.BCMPlannerError as exc:
        return _err(exc)


@mcp.tool()
def get_escalation_path_for_severity(organization_id: str, severity_level: str) -> dict[str, Any]:
    """Returns the escalation path for an organization + severity level:
    the matching escalation_triggers row(s) plus the CMT roles that should
    be notified for that severity.

    Heuristic: major/severe/catastrophic notify ALL CMT roles for the
    organization; minimal/minor/moderate notify only roles whose role_name
    or key_responsibilities suggest operational/first-response involvement
    (see crisis_management.py for the full documented heuristic).
    """
    with db.get_connection() as conn:
        return crisis_management.get_escalation_path_for_severity(conn, organization_id, severity_level)


# ---------------------------------------------------------------------------
# Phase 3 — Crisis Communication & Message Bank Builder (FR10)
# ---------------------------------------------------------------------------


@mcp.tool()
def create_stakeholder_contact(
    user_id: str, organization_id: str, stakeholder_group: str, contact_person_or_entity: str,
    primary_channel: str, backup_channel: Optional[str] = None, notification_priority: int = 1,
) -> dict[str, Any]:
    """Creates a stakeholder contact matrix entry (e.g. Internal Personnel,
    Executive Leadership, Media, Regulators, Key Vendors).
    """
    try:
        with db.get_connection() as conn:
            return crisis_communications.create_stakeholder_contact(
                conn, user_id, organization_id, stakeholder_group, contact_person_or_entity,
                primary_channel, backup_channel, notification_priority,
            )
    except crisis_communications.BCMPlannerError as exc:
        return _err(exc)


@mcp.tool()
def get_stakeholder_contact(contact_id: str) -> dict[str, Any]:
    """Reads a single stakeholder contact matrix entry by ID."""
    try:
        with db.get_connection() as conn:
            return crisis_communications.get_stakeholder_contact(conn, contact_id)
    except crisis_communications.BCMPlannerError as exc:
        return _err(exc)


@mcp.tool()
def list_stakeholder_contacts_by_organization(organization_id: str) -> list[dict[str, Any]]:
    """Lists all stakeholder contacts for an organization, ordered by notification_priority."""
    with db.get_connection() as conn:
        return crisis_communications.list_stakeholder_contacts_by_organization(conn, organization_id)


@mcp.tool()
def update_stakeholder_contact(user_id: str, contact_id: str, fields: dict[str, Any]) -> dict[str, Any]:
    """Updates mutable fields on an existing stakeholder contact matrix entry.

    `fields` may include: stakeholder_group, contact_person_or_entity,
    primary_channel, backup_channel, notification_priority.
    """
    try:
        with db.get_connection() as conn:
            return crisis_communications.update_stakeholder_contact(conn, user_id, contact_id, **fields)
    except crisis_communications.BCMPlannerError as exc:
        return _err(exc)


@mcp.tool()
def create_message_bank_entry(
    user_id: str, organization_id: str, scenario_type: str, target_audience: str,
    holding_statement_template: str, pre_approved_by_legal: bool = False,
    dispatch_channels: Optional[list[str]] = None,
) -> dict[str, Any]:
    """Creates a message_bank entry. pre_approved_by_legal defaults to False
    and should only be set True after an actual human legal review.
    """
    try:
        with db.get_connection() as conn:
            return crisis_communications.create_message_bank_entry(
                conn, user_id, organization_id, scenario_type, target_audience,
                holding_statement_template, pre_approved_by_legal, dispatch_channels,
            )
    except crisis_communications.BCMPlannerError as exc:
        return _err(exc)


@mcp.tool()
def get_message_bank_entry(message_id: str) -> dict[str, Any]:
    """Reads a single message_bank entry by ID."""
    try:
        with db.get_connection() as conn:
            return crisis_communications.get_message_bank_entry(conn, message_id)
    except crisis_communications.BCMPlannerError as exc:
        return _err(exc)


@mcp.tool()
def list_message_bank_entries_by_organization(organization_id: str) -> list[dict[str, Any]]:
    """Lists all message_bank entries for an organization."""
    with db.get_connection() as conn:
        return crisis_communications.list_message_bank_entries_by_organization(conn, organization_id)


@mcp.tool()
def update_message_bank_entry(user_id: str, message_id: str, fields: dict[str, Any]) -> dict[str, Any]:
    """Updates mutable fields on an existing message_bank entry.

    `fields` may include: scenario_type, target_audience,
    holding_statement_template, pre_approved_by_legal, dispatch_channels.
    Use this (with pre_approved_by_legal=True) to record an actual human
    legal sign-off on a draft — never implied automatically by generation.
    """
    try:
        with db.get_connection() as conn:
            return crisis_communications.update_message_bank_entry(conn, user_id, message_id, **fields)
    except crisis_communications.BCMPlannerError as exc:
        return _err(exc)


@mcp.tool()
def generate_holding_statement_draft(scenario_type: str, target_audience: str) -> dict[str, Any]:
    """Generates a rule-based DRAFT holding statement template for a
    scenario_type (power_outage, cyberattack_ddos, data_breach,
    public_transit_disruption, or a generic fallback for any other value)
    and target_audience. Contains fill-in placeholder tokens
    ({incident_summary}, {expected_resolution_time}, {contact_channel}).

    pre_approved_by_legal is ALWAYS False in the result, regardless of any
    input — see docs/crisis_communication_templates.md section 5. Does not
    write to the database; pass the result to create_message_bank_entry to
    persist it.
    """
    return crisis_communications.generate_holding_statement_draft(scenario_type, target_audience)


# ---------------------------------------------------------------------------
# Phase 4 — Modular Exercise & Test Planner (FR11)
# ---------------------------------------------------------------------------


@mcp.tool()
def create_exercise_programme(
    user_id: str, organization_id: str, title: str, annual_schedule_year: int,
    objectives: str, approved_budget: Optional[float] = None,
) -> dict[str, Any]:
    """Creates an annual exercise/test programme for an organization."""
    try:
        with db.get_connection() as conn:
            return exercise_planner.create_exercise_programme(
                conn, user_id, organization_id, title, annual_schedule_year, objectives, approved_budget,
            )
    except exercise_planner.BCMPlannerError as exc:
        return _err(exc)


@mcp.tool()
def get_exercise_programme(programme_id: str) -> dict[str, Any]:
    """Reads a single exercise programme by ID."""
    try:
        with db.get_connection() as conn:
            return exercise_planner.get_exercise_programme(conn, programme_id)
    except exercise_planner.BCMPlannerError as exc:
        return _err(exc)


@mcp.tool()
def list_exercise_programmes_by_organization(organization_id: str) -> list[dict[str, Any]]:
    """Lists all exercise programmes for an organization, most recent year first."""
    with db.get_connection() as conn:
        return exercise_planner.list_exercise_programmes_by_organization(conn, organization_id)


@mcp.tool()
def update_exercise_programme(user_id: str, programme_id: str, fields: dict[str, Any]) -> dict[str, Any]:
    """Updates mutable fields on an existing exercise programme.

    `fields` may include: title, annual_schedule_year, objectives, approved_budget.
    """
    try:
        with db.get_connection() as conn:
            return exercise_planner.update_exercise_programme(conn, user_id, programme_id, **fields)
    except exercise_planner.BCMPlannerError as exc:
        return _err(exc)


@mcp.tool()
def create_exercise(
    user_id: str, programme_id: str, category: str, title: str, planned_date: str,
    lead_facilitator: str, scenario_description: str, status: str = "Scheduled",
) -> dict[str, Any]:
    """Creates an exercise under a programme. category must be one of
    exercise_category_enum's values (discussion_based/scenario_tabletop/
    simulation/live/functional_test). planned_date is an ISO date string.
    """
    try:
        parsed_date = datetime.date.fromisoformat(planned_date)
        with db.get_connection() as conn:
            return exercise_planner.create_exercise(
                conn, user_id, programme_id, category, title, parsed_date,
                lead_facilitator, scenario_description, status,
            )
    except exercise_planner.BCMPlannerError as exc:
        return _err(exc)


@mcp.tool()
def get_exercise(exercise_id: str) -> dict[str, Any]:
    """Reads a single exercise by ID."""
    try:
        with db.get_connection() as conn:
            return exercise_planner.get_exercise(conn, exercise_id)
    except exercise_planner.BCMPlannerError as exc:
        return _err(exc)


@mcp.tool()
def list_exercises_by_programme(programme_id: str) -> list[dict[str, Any]]:
    """Lists all exercises under a programme, ordered by planned_date."""
    with db.get_connection() as conn:
        return exercise_planner.list_exercises_by_programme(conn, programme_id)


@mcp.tool()
def update_exercise(user_id: str, exercise_id: str, fields: dict[str, Any]) -> dict[str, Any]:
    """Updates mutable fields on an existing exercise.

    `fields` may include: category, title, planned_date (ISO string),
    lead_facilitator, scenario_description, status.
    """
    try:
        parsed = dict(fields)
        if "planned_date" in parsed and isinstance(parsed["planned_date"], str):
            parsed["planned_date"] = datetime.date.fromisoformat(parsed["planned_date"])
        with db.get_connection() as conn:
            return exercise_planner.update_exercise(conn, user_id, exercise_id, **parsed)
    except exercise_planner.BCMPlannerError as exc:
        return _err(exc)


@mcp.tool()
def get_disruption_scenario_template(scenario_type: str) -> dict[str, Any]:
    """Returns a pre-configured disruption scenario template (suggested
    scenario_description, suggested exercise category, suggested
    objectives) for a recognized scenario_type (power_outage,
    cyberattack_ddos, data_breach, public_transit_disruption,
    key_supplier_failure). Raises a clear error for an unrecognized
    scenario_type rather than fabricating a generic template — see
    docs/exercise_scenario_templates.md. Does not write to the database.
    """
    try:
        return exercise_planner.get_disruption_scenario_template(scenario_type)
    except exercise_planner.BCMPlannerError as exc:
        return _err(exc)


# ---------------------------------------------------------------------------
# Phase 4 — Scenario Injects & Timeline Storyboarding (FR12)
# ---------------------------------------------------------------------------


@mcp.tool()
def create_scenario_inject(
    user_id: str, exercise_id: str, sequence_number: int, time_offset_minutes: int,
    inject_title: str, inject_content: str, delivery_method: str, expected_team_action: str,
) -> dict[str, Any]:
    """Creates a scenario inject under an exercise. No ordering validation
    happens on a single insert — call get_exercise_storyboard(exercise_id)
    after authoring a full inject set to validate the storyboard.
    """
    try:
        with db.get_connection() as conn:
            return scenario_injects.create_scenario_inject(
                conn, user_id, exercise_id, sequence_number, time_offset_minutes,
                inject_title, inject_content, delivery_method, expected_team_action,
            )
    except scenario_injects.BCMPlannerError as exc:
        return _err(exc)


@mcp.tool()
def get_scenario_inject(inject_id: str) -> dict[str, Any]:
    """Reads a single scenario inject by ID."""
    try:
        with db.get_connection() as conn:
            return scenario_injects.get_scenario_inject(conn, inject_id)
    except scenario_injects.BCMPlannerError as exc:
        return _err(exc)


@mcp.tool()
def list_scenario_injects_by_exercise(exercise_id: str) -> list[dict[str, Any]]:
    """Lists all scenario injects for an exercise, ordered by
    time_offset_minutes (does not validate ordering — see
    get_exercise_storyboard for the validated read).
    """
    with db.get_connection() as conn:
        return scenario_injects.list_scenario_injects_by_exercise(conn, exercise_id)


@mcp.tool()
def update_scenario_inject(user_id: str, inject_id: str, fields: dict[str, Any]) -> dict[str, Any]:
    """Updates mutable fields on an existing scenario inject.

    `fields` may include: sequence_number, time_offset_minutes,
    inject_title, inject_content, delivery_method, expected_team_action.
    """
    try:
        with db.get_connection() as conn:
            return scenario_injects.update_scenario_inject(conn, user_id, inject_id, **fields)
    except scenario_injects.BCMPlannerError as exc:
        return _err(exc)


@mcp.tool()
def get_exercise_storyboard(exercise_id: str) -> dict[str, Any]:
    """Returns the validated, time-ordered storyboard (scenario_injects)
    for an exercise. Raises a clear error (surfaced as {"error":
    "StoryboardValidationError", ...}) if sequence_number values are
    duplicated or do not strictly increase with time_offset_minutes order
    — a facilitator authoring error, not silently allowed through.
    """
    try:
        with db.get_connection() as conn:
            return scenario_injects.get_exercise_storyboard(conn, exercise_id)
    except scenario_injects.BCMPlannerError as exc:
        return _err(exc)


@mcp.tool()
def generate_injects_from_scenario_template(user_id: str, exercise_id: str, scenario_type: str) -> list[dict[str, Any]]:
    """Auto-generates a starter set of 3-5 time-phased scenario injects for
    an existing exercise, appropriate to scenario_type (same values as
    get_disruption_scenario_template). Rule-based/templated, not
    AI-generated prose — see docs/exercise_scenario_templates.md. Raises a
    clear error for an unrecognized scenario_type or unknown exercise_id.
    """
    try:
        with db.get_connection() as conn:
            return scenario_injects.generate_injects_from_scenario_template(conn, user_id, exercise_id, scenario_type)
    except scenario_injects.BCMPlannerError as exc:
        return _err(exc)


# ---------------------------------------------------------------------------
# Phase 4 — Debrief, Hot-Debrief & Action Tracking (FR13)
# ---------------------------------------------------------------------------


@mcp.tool()
def create_exercise_debrief(
    user_id: str, exercise_id: str, hot_debrief_summary: str,
    strengths_observed: Optional[str] = None, weaknesses_observed: Optional[str] = None,
    opportunities_for_improvement: Optional[str] = None, identified_threats_risks: Optional[str] = None,
    overall_rating: Optional[str] = None,
) -> dict[str, Any]:
    """Creates a debrief for an exercise. Only one debrief is permitted per
    exercise_id — a second attempt returns a clear DuplicateDebriefError,
    not a raw database constraint traceback.
    """
    try:
        with db.get_connection() as conn:
            return exercise_debrief.create_exercise_debrief(
                conn, user_id, exercise_id, hot_debrief_summary, strengths_observed,
                weaknesses_observed, opportunities_for_improvement, identified_threats_risks, overall_rating,
            )
    except exercise_debrief.BCMPlannerError as exc:
        return _err(exc)


@mcp.tool()
def get_exercise_debrief(debrief_id: str) -> dict[str, Any]:
    """Reads a single exercise debrief by ID."""
    try:
        with db.get_connection() as conn:
            return exercise_debrief.get_exercise_debrief(conn, debrief_id)
    except exercise_debrief.BCMPlannerError as exc:
        return _err(exc)


@mcp.tool()
def get_exercise_debrief_by_exercise(exercise_id: str) -> Optional[dict[str, Any]]:
    """Reads the debrief for an exercise_id, if one exists. Returns None
    (not an error) if no debrief has been recorded yet.
    """
    with db.get_connection() as conn:
        return exercise_debrief.get_exercise_debrief_by_exercise(conn, exercise_id)


@mcp.tool()
def update_exercise_debrief(user_id: str, debrief_id: str, fields: dict[str, Any]) -> dict[str, Any]:
    """Updates mutable fields on an existing exercise debrief.

    `fields` may include: hot_debrief_summary, strengths_observed,
    weaknesses_observed, opportunities_for_improvement,
    identified_threats_risks, overall_rating.
    """
    try:
        with db.get_connection() as conn:
            return exercise_debrief.update_exercise_debrief(conn, user_id, debrief_id, **fields)
    except exercise_debrief.BCMPlannerError as exc:
        return _err(exc)


@mcp.tool()
def create_capa_action_item(
    user_id: str, debrief_id: str, gap_description: str, corrective_action_required: str,
    assigned_owner: str, due_date: str, status: str = "Open", completion_date: Optional[str] = None,
) -> dict[str, Any]:
    """Creates a CAPA (Corrective/Preventive Action) item under a debrief.
    due_date and completion_date are ISO date strings.
    """
    try:
        parsed_due = datetime.date.fromisoformat(due_date)
        parsed_completion = datetime.date.fromisoformat(completion_date) if completion_date else None
        with db.get_connection() as conn:
            return exercise_debrief.create_capa_action_item(
                conn, user_id, debrief_id, gap_description, corrective_action_required,
                assigned_owner, parsed_due, status, parsed_completion,
            )
    except exercise_debrief.BCMPlannerError as exc:
        return _err(exc)


@mcp.tool()
def get_capa_action_item(action_id: str) -> dict[str, Any]:
    """Reads a single CAPA action item by ID."""
    try:
        with db.get_connection() as conn:
            return exercise_debrief.get_capa_action_item(conn, action_id)
    except exercise_debrief.BCMPlannerError as exc:
        return _err(exc)


@mcp.tool()
def list_capa_action_items_by_debrief(debrief_id: str) -> list[dict[str, Any]]:
    """Lists all CAPA action items for a debrief, ordered by due_date."""
    with db.get_connection() as conn:
        return exercise_debrief.list_capa_action_items_by_debrief(conn, debrief_id)


@mcp.tool()
def update_capa_action_item(user_id: str, action_id: str, fields: dict[str, Any]) -> dict[str, Any]:
    """Updates mutable fields on an existing CAPA action item.

    `fields` may include: gap_description, corrective_action_required,
    assigned_owner, due_date (ISO string), status, completion_date (ISO string).
    """
    try:
        parsed = dict(fields)
        if "due_date" in parsed and isinstance(parsed["due_date"], str):
            parsed["due_date"] = datetime.date.fromisoformat(parsed["due_date"])
        if "completion_date" in parsed and isinstance(parsed["completion_date"], str):
            parsed["completion_date"] = datetime.date.fromisoformat(parsed["completion_date"])
        with db.get_connection() as conn:
            return exercise_debrief.update_capa_action_item(conn, user_id, action_id, **parsed)
    except exercise_debrief.BCMPlannerError as exc:
        return _err(exc)


@mcp.tool()
def get_overdue_capa_items(organization_id: str) -> list[dict[str, Any]]:
    """Returns all CAPA action items for an organization that are past
    their due_date and not yet completed (status not Completed/Verified) —
    surfaces follow-through gaps across every exercise/debrief.
    """
    with db.get_connection() as conn:
        return exercise_debrief.get_overdue_capa_items(conn, organization_id)


@mcp.tool()
def get_open_capa_items(organization_id: str) -> list[dict[str, Any]]:
    """Returns all CAPA action items for an organization not yet completed
    (status not Completed/Verified), regardless of due_date — the full
    open/in-progress backlog. See get_overdue_capa_items for the stricter
    past-due subset.
    """
    with db.get_connection() as conn:
        return exercise_debrief.get_open_capa_items(conn, organization_id)


# ---------------------------------------------------------------------------
# Phase 5 — Governance & Lifecycle: multi-tier sign-off workflow (FR14)
# ---------------------------------------------------------------------------


@mcp.tool()
def create_sign_off_chain(
    user_id: str, entity_type: str, entity_id: str, tiers: Optional[list[dict[str, Any]]] = None,
) -> list[dict[str, Any]]:
    """Creates a multi-tier sign-off chain for a governed entity
    (bia_assessment / bc_plan / recovery_strategy / crisis_management_plan /
    exercise_debrief), all tiers starting 'pending'. Defaults to the
    standard two-tier (process owner -> top management) chain if `tiers`
    is not given; otherwise pass a list of {"sequence_order": int,
    "required_role": str} dicts. Raises a clear error if a chain already
    exists for this entity.
    """
    try:
        with db.get_connection() as conn:
            return governance.create_sign_off_chain(conn, user_id, entity_type, entity_id, tiers)
    except governance.BCMPlannerError as exc:
        return [_err(exc)]


@mcp.tool()
def get_sign_off_approval(approval_id: str) -> dict[str, Any]:
    """Reads a single sign_off_approvals tier row by ID."""
    try:
        with db.get_connection() as conn:
            return governance.get_sign_off_approval(conn, approval_id)
    except governance.BCMPlannerError as exc:
        return _err(exc)


@mcp.tool()
def list_sign_off_approvals_for_entity(entity_type: str, entity_id: str) -> list[dict[str, Any]]:
    """Lists every sign-off tier for an entity, ordered by sequence_order
    ascending (tier 1 first)."""
    with db.get_connection() as conn:
        return governance.list_sign_off_approvals_for_entity(conn, entity_type, entity_id)


@mcp.tool()
def get_current_pending_tier(entity_type: str, entity_id: str) -> Optional[dict[str, Any]]:
    """Returns the lowest-sequence_order tier still 'pending' for an
    entity (the next gate that needs to be actioned), or None if every
    tier has already been decided.
    """
    with db.get_connection() as conn:
        return governance.get_current_pending_tier(conn, entity_type, entity_id)


@mcp.tool()
def get_sign_off_status(entity_type: str, entity_id: str) -> dict[str, Any]:
    """Returns the overall sign-off status for an entity: 'not_started',
    'in_progress', 'approved', 'rejected', or 'returned_for_revision',
    plus the full tier list and the current pending tier (if any).
    """
    with db.get_connection() as conn:
        return governance.get_sign_off_status(conn, entity_type, entity_id)


@mcp.tool()
def submit_sign_off_decision(
    user_id: str, entity_type: str, entity_id: str, sequence_order: int,
    decision: str, comments: Optional[str] = None,
) -> dict[str, Any]:
    """Records a decision ('approved' / 'rejected' / 'returned_for_revision')
    for one tier of an entity's sign-off chain. Blocks with a clear
    SignOffSequenceError if any earlier tier has not yet been approved,
    and with SignOffAlreadyDecidedError if the target tier was already
    decided. Requires the acting user to hold that tier's required_role
    (or 'admin').
    """
    try:
        with db.get_connection() as conn:
            return governance.submit_sign_off_decision(
                conn, user_id, entity_type, entity_id, sequence_order, decision, comments,
            )
    except governance.BCMPlannerError as exc:
        return _err(exc)


@mcp.tool()
def restart_sign_off_chain(user_id: str, entity_type: str, entity_id: str) -> list[dict[str, Any]]:
    """Resets every tier of an entity's sign-off chain back to 'pending' —
    the explicit, auditable way to restart a sign-off cycle after a
    rejection or a return-for-revision, once the underlying document has
    been revised.
    """
    try:
        with db.get_connection() as conn:
            return governance.restart_sign_off_chain(conn, user_id, entity_type, entity_id)
    except governance.BCMPlannerError as exc:
        return [_err(exc)]


# ---------------------------------------------------------------------------
# Phase 5 — Governance & Lifecycle: version control & review scheduler (FR15)
# ---------------------------------------------------------------------------


@mcp.tool()
def compute_next_review_date(base_date: str, review_frequency_months: int) -> str:
    """Adds review_frequency_months calendar months to base_date (ISO date
    string), clamping the day-of-month to the last valid day of the
    target month where needed (e.g. 2026-01-31 + 1 month -> 2026-02-28).
    Returns an ISO date string.
    """
    result = governance.compute_next_review_date(
        datetime.date.fromisoformat(base_date), review_frequency_months,
    )
    return result.isoformat()


@mcp.tool()
def create_review_schedule(
    user_id: str, entity_type: str, entity_id: str,
    review_frequency_months: int = 12,
    last_reviewed_date: Optional[str] = None,
    next_review_date: Optional[str] = None,
    review_trigger_type: str = "periodic",
    trigger_event_description: Optional[str] = None,
    notes: Optional[str] = None,
) -> dict[str, Any]:
    """Creates a document_review_schedule row for a governed entity.
    For review_trigger_type='periodic', next_review_date is
    auto-computed from last_reviewed_date (or today) + 
    review_frequency_months if not supplied. For 'event_driven',
    next_review_date must be supplied explicitly. Dates are ISO strings.
    """
    try:
        with db.get_connection() as conn:
            return governance.create_review_schedule(
                conn, user_id, entity_type, entity_id, review_frequency_months,
                datetime.date.fromisoformat(last_reviewed_date) if last_reviewed_date else None,
                datetime.date.fromisoformat(next_review_date) if next_review_date else None,
                review_trigger_type, trigger_event_description, notes,
            )
    except governance.BCMPlannerError as exc:
        return _err(exc)


@mcp.tool()
def get_review_schedule(schedule_id: str) -> dict[str, Any]:
    """Reads a single document_review_schedule row by ID."""
    try:
        with db.get_connection() as conn:
            return governance.get_review_schedule(conn, schedule_id)
    except governance.BCMPlannerError as exc:
        return _err(exc)


@mcp.tool()
def get_review_schedule_for_entity(entity_type: str, entity_id: str) -> Optional[dict[str, Any]]:
    """Reads the review schedule for an entity, if one exists. Returns
    None (not an error) if no schedule has been created yet.
    """
    with db.get_connection() as conn:
        return governance.get_review_schedule_for_entity(conn, entity_type, entity_id)


@mcp.tool()
def update_review_schedule(user_id: str, schedule_id: str, fields: dict[str, Any]) -> dict[str, Any]:
    """Updates mutable fields on an existing document_review_schedule row.

    `fields` may include: review_frequency_months, last_reviewed_date
    (ISO string), next_review_date (ISO string), review_trigger_type,
    trigger_event_description, notes.
    """
    try:
        parsed = dict(fields)
        for date_field in ("last_reviewed_date", "next_review_date"):
            if date_field in parsed and isinstance(parsed[date_field], str):
                parsed[date_field] = datetime.date.fromisoformat(parsed[date_field])
        with db.get_connection() as conn:
            return governance.update_review_schedule(conn, user_id, schedule_id, **parsed)
    except governance.BCMPlannerError as exc:
        return _err(exc)


@mcp.tool()
def mark_review_completed(
    user_id: str, schedule_id: str,
    reviewed_date: Optional[str] = None, next_review_date: Optional[str] = None,
) -> dict[str, Any]:
    """Records that a scheduled review happened: sets last_reviewed_date
    (default: today) and advances next_review_date (auto-computed for
    'periodic' schedules, must be supplied explicitly for 'event_driven'
    ones). Dates are ISO strings.
    """
    try:
        with db.get_connection() as conn:
            return governance.mark_review_completed(
                conn, user_id, schedule_id,
                datetime.date.fromisoformat(reviewed_date) if reviewed_date else None,
                datetime.date.fromisoformat(next_review_date) if next_review_date else None,
            )
    except governance.BCMPlannerError as exc:
        return _err(exc)


@mcp.tool()
def list_upcoming_review_schedules(within_days: int = 90) -> list[dict[str, Any]]:
    """Returns document_review_schedule rows due within the next
    within_days days, ordered by next_review_date ascending.
    """
    with db.get_connection() as conn:
        return governance.list_upcoming_review_schedules(conn, within_days)


@mcp.tool()
def get_overdue_review_schedules() -> list[dict[str, Any]]:
    """Returns document_review_schedule rows past their next_review_date,
    ordered by next_review_date ascending (most overdue first).
    """
    with db.get_connection() as conn:
        return governance.get_overdue_review_schedules(conn)


@mcp.tool()
def create_document_version(
    user_id: str, entity_type: str, entity_id: str, version_label: str,
    snapshot_json: dict[str, Any], change_summary: Optional[str] = None,
) -> dict[str, Any]:
    """Creates a document_versions row — a full JSONB snapshot of an
    entity tagged with a human-assigned version_label (e.g. '1.0').
    Raises a clear error if this version_label already exists for the
    entity.
    """
    try:
        with db.get_connection() as conn:
            return governance.create_document_version(
                conn, user_id, entity_type, entity_id, version_label, snapshot_json, change_summary,
            )
    except governance.BCMPlannerError as exc:
        return _err(exc)


@mcp.tool()
def get_document_version(version_id: str) -> dict[str, Any]:
    """Reads a single document_versions row by ID."""
    try:
        with db.get_connection() as conn:
            return governance.get_document_version(conn, version_id)
    except governance.BCMPlannerError as exc:
        return _err(exc)


@mcp.tool()
def list_document_versions_for_entity(entity_type: str, entity_id: str) -> list[dict[str, Any]]:
    """Returns every document_versions row for an entity, newest first —
    the full maintenance/version history for a single BIA/BCP/CMP/test
    report.
    """
    with db.get_connection() as conn:
        return governance.list_document_versions_for_entity(conn, entity_type, entity_id)


@mcp.tool()
def get_latest_document_version(entity_type: str, entity_id: str) -> Optional[dict[str, Any]]:
    """Returns the most recent document_versions row for an entity, or
    None if it has never been versioned.
    """
    with db.get_connection() as conn:
        return governance.get_latest_document_version(conn, entity_type, entity_id)


@mcp.tool()
def record_maintenance_update(
    user_id: str, entity_type: str, entity_id: str, change_summary: str,
    snapshot_json: Optional[dict[str, Any]] = None, version_label: Optional[str] = None,
) -> dict[str, Any]:
    """Logs a maintenance update for a governed entity (FR15): creates a
    document_versions row, auto-computing the next minor version_label
    (e.g. '1.0' -> '1.1') if one isn't given. snapshot_json defaults to
    an empty object if only a change_summary is being logged.
    """
    try:
        with db.get_connection() as conn:
            return governance.record_maintenance_update(
                conn, user_id, entity_type, entity_id, change_summary, snapshot_json, version_label,
            )
    except governance.BCMPlannerError as exc:
        return _err(exc)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
