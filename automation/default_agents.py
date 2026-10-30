"""Curated starter fleet of 20 bounded specialist-agent profiles.

These profiles share Yuki's existing four text-only worker roles. They are
metadata/configuration, not 20 independent runtimes or grants of tool access.
"""
from __future__ import annotations

from automation.sqlite_agent_registry import SQLiteAgentRegistry
from core.agent_contracts import AgentSpec


_STARTER_PROFILES = (
    ("research-analyst", "research", "Analyzes supplied evidence and separates facts from assumptions.", {"research:analyze"}),
    ("market-researcher", "research", "Structures market evidence, segments, and unknowns.", {"market:research"}),
    ("technical-researcher", "research", "Evaluates technical options from supplied materials.", {"technology:research"}),
    ("security-researcher", "research", "Reviews supplied designs for security risks and mitigations.", {"security:review"}),
    ("documentation-reader", "research", "Extracts requirements and decisions from supplied documentation.", {"documentation:analyze"}),
    ("code-architect", "coding", "Proposes modular architecture and implementation boundaries.", {"architecture:design"}),
    ("backend-engineer", "coding", "Proposes backend services, APIs, and data-layer changes.", {"backend:design"}),
    ("frontend-engineer", "coding", "Proposes accessible frontend components and interaction flows.", {"frontend:design"}),
    ("database-engineer", "coding", "Reviews schemas, migrations, and data integrity strategies.", {"database:design"}),
    ("devops-engineer", "coding", "Proposes CI/CD, deployment, and operational improvements.", {"devops:design"}),
    ("security-engineer", "coding", "Proposes secure-by-default controls and remediation plans.", {"security:design"}),
    ("performance-engineer", "coding", "Analyzes bottlenecks and proposes measurable optimizations.", {"performance:design"}),
    ("api-integrator", "coding", "Designs integrations and resilient API boundaries.", {"integration:design"}),
    ("test-strategist", "testing", "Creates risk-based test plans and acceptance criteria.", {"test:design"}),
    ("unit-test-designer", "testing", "Designs focused unit tests and edge-case coverage.", {"test:unit"}),
    ("integration-test-designer", "testing", "Designs integration and contract test scenarios.", {"test:integration"}),
    ("security-test-designer", "testing", "Designs abuse, authorization, and security regression tests.", {"test:security"}),
    ("qa-reviewer", "testing", "Reviews quality risks and test completeness.", {"quality:review"}),
    ("release-reporter", "reporting", "Produces release readiness reports from verified evidence.", {"release:report"}),
    ("project-coordinator", "reporting", "Summarizes work status, dependencies, and delivery risks.", {"project:coordinate"}),
)


def starter_agent_specs() -> tuple[AgentSpec, ...]:
    """Return 19 specialists followed by a coordinator allowed to delegate to them."""
    specialists = tuple(
        AgentSpec(
            agent_id=agent_id,
            role=role,
            description=description,
            capabilities=frozenset(capabilities),
            max_concurrency=None,
            timeout_seconds=None,
            allow_cloud=False,
        )
        for agent_id, role, description, capabilities in _STARTER_PROFILES
    )
    coordinator = AgentSpec(
        agent_id="yuki-coordinator",
        role="reporting",
        description="Coordinates the starter specialist fleet and delegates bounded tasks.",
        capabilities=frozenset({"agent:delegate", "project:coordinate"}),
        delegation_targets=frozenset(spec.agent_id for spec in specialists),
        max_concurrency=None,
        timeout_seconds=None,
        allow_cloud=False,
    )
    return specialists + (coordinator,)


def seed_starter_agents(registry: SQLiteAgentRegistry) -> tuple[AgentSpec, ...]:
    """Idempotently seed the 20 profiles; reject conflicting existing definitions."""
    if not isinstance(registry, SQLiteAgentRegistry):
        raise TypeError("registry must be a SQLiteAgentRegistry.")
    seeded = []
    for spec in starter_agent_specs():
        existing = registry.get(spec.agent_id)
        if existing is None:
            registry.register(spec)
            seeded.append(spec)
        elif existing != spec:
            raise ValueError(f"starter agent {spec.agent_id!r} conflicts with an existing definition.")
    return tuple(seeded)
