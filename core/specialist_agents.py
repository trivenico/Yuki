"""Declarative catalog of Yuki's first 100 specialist agent roles.

The catalog is metadata only: it does not create workers, grant tools, or execute
model output. Execution must go through the shared runtime and policy gate.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AgentSpec:
    agent_id: str
    family: str
    purpose: str
    capabilities: tuple[str, ...] = ("plan", "analyze", "report")
    allowed_tools: tuple[str, ...] = ()
    requires_approval: bool = True


# Each family has ten distinct role definitions. Keep these entries declarative;
# executable capabilities are assigned separately by the reviewed tool registry.
_FAMILIES: tuple[tuple[str, tuple[tuple[str, str], ...]], ...] = (
    ("core", (
        ("orchestrator", "Break goals into bounded tasks and coordinate approved workers."),
        ("planner", "Create dependency-aware plans with explicit acceptance criteria."),
        ("task_router", "Route tasks to suitable specialists using declared metadata."),
        ("context_manager", "Prepare bounded, relevant context for a task."),
        ("memory_curator", "Propose memory updates while respecting retention policy."),
        ("knowledge_curator", "Organize approved knowledge sources and provenance."),
        ("session_manager", "Track conversation and task lifecycle state."),
        ("workflow_designer", "Compose deterministic workflows from approved capabilities."),
        ("policy_advisor", "Explain policy decisions without granting permissions."),
        ("quality_coordinator", "Aggregate specialist results and verify completion criteria."),
    )),
    ("engineering", (
        ("software_architect", "Design service boundaries, interfaces, and technical trade-offs."),
        ("backend_engineer", "Implement reviewed backend changes and API contracts."),
        ("frontend_engineer", "Implement accessible user interfaces and interaction states."),
        ("fullstack_engineer", "Coordinate frontend and backend implementation."),
        ("database_engineer", "Design schemas, migrations, indexes, and data lifecycle."),
        ("api_engineer", "Design versioned APIs, validation, and compatibility behavior."),
        ("mobile_engineer", "Plan and implement mobile application features."),
        ("desktop_engineer", "Build desktop shells over the shared application services."),
        ("cli_engineer", "Build robust command-line workflows and terminal UX."),
        ("code_reviewer", "Review diffs for correctness, maintainability, and regressions."),
    )),
    ("research", (
        ("web_researcher", "Gather public sources and summarize evidence with citations."),
        ("literature_reviewer", "Compare research papers and identify methodological limits."),
        ("competitive_analyst", "Compare products, positioning, and publicly documented features."),
        ("market_researcher", "Analyze market segments and validate assumptions."),
        ("fact_checker", "Verify factual claims against independent sources."),
        ("source_evaluator", "Assess source quality, recency, and provenance."),
        ("technical_scout", "Evaluate technical projects and upstream dependencies."),
        ("patent_scout", "Organize public patent information for human review."),
        ("trend_analyst", "Identify evidence-backed patterns in supplied information."),
        ("research_synthesizer", "Combine findings while preserving disagreement and uncertainty."),
    )),
    ("security", (
        ("security_architect", "Design threat models and security boundaries."),
        ("application_security", "Review application code for common security weaknesses."),
        ("cloud_security", "Review cloud identity, network, and resource configuration."),
        ("dependency_auditor", "Assess dependency risk, provenance, and known advisories."),
        ("privacy_reviewer", "Review data minimization, retention, and privacy controls."),
        ("prompt_injection_reviewer", "Test handling of untrusted instructions and retrieved content."),
        ("identity_access_reviewer", "Review authentication, authorization, and least privilege."),
        ("incident_analyst", "Structure incident timelines and response recommendations."),
        ("defensive_network_analyst", "Analyze authorized defensive network telemetry."),
        ("security_test_designer", "Design safe, scoped security acceptance tests."),
    )),
    ("business", (
        ("product_manager", "Translate user needs into prioritized product requirements."),
        ("product_analyst", "Define product metrics and analyze feature outcomes."),
        ("startup_strategist", "Evaluate startup strategy, risks, and sequencing."),
        ("business_analyst", "Map business processes and measurable requirements."),
        ("project_manager", "Track milestones, dependencies, and delivery risks."),
        ("operations_analyst", "Improve repeatable operational workflows."),
        ("customer_success", "Prepare customer onboarding and support guidance."),
        ("sales_enablement", "Create evidence-based sales enablement material."),
        ("pricing_analyst", "Evaluate pricing models and unit-economics assumptions."),
        ("compliance_coordinator", "Organize compliance requirements for qualified review."),
    )),
    ("data", (
        ("data_analyst", "Explore datasets and communicate descriptive findings."),
        ("data_engineer", "Design reliable data ingestion and transformation pipelines."),
        ("analytics_engineer", "Model trusted analytical datasets and metrics."),
        ("ml_engineer", "Design and evaluate machine-learning pipelines."),
        ("ml_researcher", "Compare model methods, benchmarks, and limitations."),
        ("evaluation_engineer", "Build reproducible model and agent evaluations."),
        ("experiment_designer", "Design controlled experiments and measurement plans."),
        ("visualization_analyst", "Choose clear visual encodings for supplied data."),
        ("database_performance", "Diagnose query plans and database performance."),
        ("data_quality", "Define data validation, lineage, and quality checks."),
    )),
    ("content", (
        ("technical_writer", "Write technical documentation from verified implementation."),
        ("editor", "Improve clarity, structure, and consistency of supplied drafts."),
        ("copywriter", "Draft audience-aware copy with factual claims checked."),
        ("content_strategist", "Plan content themes, formats, and publishing cadence."),
        ("seo_researcher", "Analyze search intent and propose ethical SEO improvements."),
        ("translator", "Translate content while preserving meaning and terminology."),
        ("localizer", "Adapt product content to locale and cultural conventions."),
        ("presentation_designer", "Structure presentations around audience and narrative."),
        ("video_scriptwriter", "Draft scripts and shot plans for video production."),
        ("documentation_auditor", "Find stale, missing, or contradictory documentation."),
    )),
    ("personal", (
        ("personal_assistant", "Organize user-approved personal tasks and reminders."),
        ("calendar_planner", "Propose schedules while respecting user constraints."),
        ("email_drafter", "Draft messages for explicit user review before sending."),
        ("meeting_summarizer", "Summarize supplied meeting notes and action items."),
        ("learning_coach", "Create adaptive study plans and practice activities."),
        ("interview_coach", "Prepare role-specific interview practice and feedback."),
        ("career_coach", "Support career planning with transparent assumptions."),
        ("travel_planner", "Plan trips using current, verified travel information."),
        ("household_planner", "Organize household tasks and maintenance schedules."),
        ("accessibility_advisor", "Review content and workflows for accessibility."),
    )),
    ("testing", (
        ("test_architect", "Design layered test strategies and coverage boundaries."),
        ("unit_test_engineer", "Create focused deterministic unit tests."),
        ("integration_test_engineer", "Test service boundaries and dependency contracts."),
        ("end_to_end_tester", "Design user-journey acceptance tests."),
        ("performance_tester", "Measure latency, throughput, and resource use."),
    )),
    ("cloud_devops", (
        ("cloud_architect", "Design secure, cost-aware cloud deployment topology."),
        ("devops_engineer", "Build repeatable build, test, and release automation."),
        ("sre_engineer", "Define reliability objectives, alerts, and incident practices."),
        ("infrastructure_as_code", "Review declarative infrastructure and drift controls."),
        ("container_engineer", "Build hardened, reproducible container workflows."),
        ("release_engineer", "Manage versioning, artifacts, and release promotion."),
        ("observability_engineer", "Design metrics, logs, traces, and redaction."),
        ("cost_optimizer", "Analyze infrastructure cost and efficiency trade-offs."),
        ("backup_recovery", "Design and validate backup and restoration procedures."),
        ("oracle_cloud_specialist", "Plan Oracle Cloud deployments within budget constraints."),
    )),
)

def _build_catalog() -> tuple[AgentSpec, ...]:
    result: list[AgentSpec] = []
    for family, entries in _FAMILIES:
        for role, purpose in entries:
            result.append(AgentSpec(
                agent_id=f"{family}.{role}",
                family=family,
                purpose=purpose,
            ))
    ids = [spec.agent_id for spec in result]
    if len(result) != 100 or len(ids) != len(set(ids)):
        raise RuntimeError("Specialist agent catalog must contain exactly 100 unique roles.")
    return tuple(result)


AGENT_CATALOG = _build_catalog()
_AGENTS_BY_ID = {spec.agent_id: spec for spec in AGENT_CATALOG}


def list_agents(*, family: str | None = None) -> tuple[AgentSpec, ...]:
    """Return immutable role metadata, optionally filtered by family."""
    if family is None:
        return AGENT_CATALOG
    normalized = family.strip().lower()
    return tuple(spec for spec in AGENT_CATALOG if spec.family == normalized)


def get_agent(agent_id: str) -> AgentSpec | None:
    """Look up a role by its stable family.role identifier."""
    if not isinstance(agent_id, str):
        return None
    return _AGENTS_BY_ID.get(agent_id.strip().lower())


def list_families() -> tuple[str, ...]:
    """Return the stable family identifiers in catalog order."""
    return tuple(family for family, _ in _FAMILIES)
