# BCM Continuity Planner — High-Level System Requirements

Source: Peter van Walsem, high-level requirements input (2026-09-22).

## Functional Requirements

1. **Scope & Hierarchy Mapping**: The system shall enable users to register and structure organizational scope across Product/Service BIAs, Process BIAs, and Activity BIAs, establishing parent-child relationships and mapping internal and external vendor dependencies.
2. **Impact Category & Threshold Configuration**: The system shall provide configurable impact assessment matrices across financial, operational, reputational, legal, and regulatory dimensions, allowing customized severity thresholds for each disruption level.
3. **MTPD & RTO Calculation Engine**: The system shall evaluate operational impacts over time to determine the Maximum Tolerable Period of Disruption (MTPD / MTD) and guide users in assigning Recovery Time Objectives (RTO), enforcing the business logic that RTO must be less than MTPD.
4. **RPO & MBCO Parameter Assignment**: The system shall capture Recovery Point Objectives (RPO / maximum tolerable data loss) for critical records/systems and set Minimum Business Continuity Objectives (MBCO) defining acceptable delivery capacity during disruptions.
5. **Resource Inventory & Dependency Tracking**: The system shall allow users to catalog and link required resources — including key personnel, technology applications, physical facilities, equipment, and priority suppliers — to each prioritized business activity.
6. **Gap Analysis & Strategy Selection**: The system shall perform gap analyses comparing current operational recovery capabilities against target RTOs/RPOs, highlight single points of failure, and assist in selecting recovery strategies (e.g., Active-Active, Hot/Warm/Cold Standby, or Manual Workarounds).
7. **BCP Template Builder & Workflow Generator**: The system shall automatically populate standardized strategic, tactical, and operational Business Continuity Plans (BCPs) containing clear, role-based action steps, invocation criteria, and alternate facility procedures.
8. **Return to BAU (Business-As-Usual) Module**: The system shall facilitate the drafting of restoration and transition procedures to guide the step-by-step return from alternate recovery operations back to primary or new permanent resources.
9. **Crisis Management Team (CMT) & Escalation Mapping**: The system shall provide role definition tools for the Crisis Management Team — including Crisis Communication Leaders, Legal Counsel, and IT/Security heads — and automate escalation triggers based on incident severity.
10. **Crisis Communication & Message Bank Builder**: The system shall generate Crisis Communication Plans featuring stakeholder contact matrices, pre-approved holding statements/message banks, and multi-channel dispatch rules (social media, press releases, internal alerts).
11. **Modular Exercise & Test Planner**: The system shall support exercise planning across discussion-based, scenario/tabletop, simulation, live, and functional test categories, incorporating pre-configured disruption templates such as power outages, cyberattacks, or transit disruptions.
12. **Scenario Injects & Timeline Storyboarding**: The system shall enable exercise facilitators to build time-phased storyboards and inject dynamic scenario events (e.g., social media leaks, supplier failures) during exercise execution.
13. **Debrief, Hot-Debrief & Action Tracking**: The system shall log post-exercise "hot-debrief" feedback, capture identified plan gaps, and generate Corrective Action and Preventive Action (CAPA) tracking logs with assigned owners and completion deadlines.
14. **Management Review & Digital Sign-Off Workflow**: The system shall enforce multi-tier approval workflows requiring explicit sign-off from process owners and top management on BIA findings, recovery strategies, and published plans.
15. **Version Control & Maintenance Scheduler**: The system shall maintain full document version histories, schedule recurring review cycles (e.g., annual or event-driven), and log all maintenance updates across BIA parameters, BCPs, CMPs, and test reports.

## Non-Functional Requirements

1. **Role-Based Security & Access Control (RBAC)**: The system shall enforce strict Role-Based Access Control and Principle of Least Privilege to restrict access to sensitive BIA impact figures, strategy details, and personnel emergency contacts.
2. **Data Confidentiality & Encryption**: All BIA entries, plan templates, and crisis contact repositories shall be encrypted at rest using AES-256 and in transit using TLS 1.3 to protect proprietary operational data.
3. **High Availability & Service Availability**: The webtool shall achieve a minimum service availability SLA of 99.9%, utilizing redundant cloud infrastructure to remain operational during widespread regional disruptions.
4. **Offline Resilience & Local Exportability**: The platform shall allow local offline caching and rapid export (e.g., offline PWA or encrypted PDF/JSON bundles) of emergency playbooks, CMP contact sheets, and BCP action steps for access during internet or power outages.
5. **System Performance & Latency**: Calculations for MTPD/RTO metric rollups, gap identification, and plan generation scripts shall execute in under 2 seconds for standard enterprise data sets.
6. **Scalability & Scope Adaptability**: The application architecture shall scale seamlessly from single-site small businesses to multi-tenant, multi-national enterprise structures with hundreds of distinct business units.
7. **Usability & Low-Stress User Experience (UX)**: The interface for incident invocation, emergency notification dispatches, and quick-action playbooks shall feature direct, unambiguous, and uncluttered navigation optimized for high-pressure emergency situations.
8. **Standards Compliance & Alignment**: The platform's terminology, data schema, and workflow sequences shall align strictly with standard frameworks, including ISO 22301:2019, ISO TS 22317, NIST SP 800-34 Rev 1, and BCI Good Practice Guidelines 7.0.
9. **Auditability & Immutable Logging**: The system shall maintain immutable, timestamped audit logs recording every modification made to recovery metrics, strategy approvals, role assignments, and plan text updates.
10. **Interoperability & Data Integration**: The tool shall provide REST APIs and import/export capabilities (CSV, JSON, PDF, DOCX) to integrate smoothly with HR management systems, CMDB/asset databases, and CXM/social listening platforms.
11. **Data Reliability & Continuous Backups**: Platform database state shall be backed up continuously with automated failover capabilities, targeting an infrastructure Recovery Point Objective (RPO) of zero data loss.
12. **Modular Maintainability**: The application code shall be structured in modular units — separating the BIA analytics engine, plan document generators, crisis communication modules, and exercise managers — to facilitate independent maintenance and updates.
13. **Multilingual & Localization Support**: The user interface and output templates shall support multi-language localizations to enable coordinated emergency response across geographically diverse teams.
14. **Broadcast Alert Latency**: Automated emergency notifications dispatched via the crisis communication module shall be delivered to incident responders within 60 seconds across primary communication channels (SMS, email, push notification).
15. **System Recoverability (Platform DR)**: The hosting web platform itself shall possess an Information System Contingency Plan (ISCP) and Disaster Recovery Plan (DRP) ensuring an application RTO of under 4 hours and RPO of under 1 hour in the event of cloud provider infrastructure failure.

## Scope Decision (2026-09-22)

This is a **personal portfolio/demo tool** for Peter van Walsem to establish himself as a BCM specialist in the market. It is **not** intended for production use within NN Group or any employer. Consequently:

- SaaS-scale NFRs (99.9% SLA, multi-tenant architecture, <60s broadcast alerting, platform ISCP/DRP with Postgres/queue/redundancy) are **deferred** — not built for v1, revisit only if commercial/multi-tenant SaaS intent emerges later.
- Standards alignment (ISO 22301:2019, ISO/TS 22317, NIST SP 800-34 Rev 1, BCI GPG 7.0 terminology and data schema) **is** taken seriously from Phase 0 — this is the credibility layer for a BCM specialist positioning tool and costs nothing extra to build in correctly from the start.
- Basic security hygiene (RBAC scaffold, append-only audit log, local encryption at rest for sensitive fields) is included from Phase 0 as good practice, without enterprise-grade infrastructure overhead.

## Prior Art Check (2026-09-22)

GitHub search performed before starting the build. No open-source project combines a BIA engine (MTPD/RTO/RPO/MBCO with enforced business rules) + BCP generator + crisis management + exercise planner in one tool:

- `fstelte/Business-Impact-Assessment` (Python/Flask, SQLite/MariaDB BIA app) — archived by its author 2025-06-19 ("new solution coming"); dead end.
- `riboseinc/bacman` — ISO 22301-aligned plan/procedure/drill-record manager, YAML-based; no BIA engine, no RTO/MTPD calculation logic.
- `capetron/business-continuity-plan-template` — static document template, not software.
- `jontever/bcp-generator` — AI wizard, but scoped to UK SMB compliance (Cabinet Office/NCSC/BS 65000:2022), not ISO 22301/NIST 800-34 depth.
- Remaining results were curated lists, policy docs, or unrelated "impact analysis" (code change impact) tools.

Conclusion: no serious open-source competitor exists for this specific combination. This is a genuine white space and a strong demo/portfolio argument.
