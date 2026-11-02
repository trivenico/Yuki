"""Curated starter fleet of 100 bounded agent profiles (99 specialists plus coordinator).

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

    ("competitor-analyst", "research", "Compares supplied competitor evidence and identifies positioning gaps.", {"competitor:analyze"}),
    ("customer-researcher", "research", "Synthesizes supplied customer interviews and feedback themes.", {"customer:research"}),
    ("industry-analyst", "research", "Maps industry structure, trends, and uncertainties from supplied evidence.", {"industry:analyze"}),
    ("academic-researcher", "research", "Reviews supplied academic material and summarizes methods and findings.", {"academic:research"}),
    ("patent-researcher", "research", "Organizes supplied patent documents and identifies technical themes.", {"patent:analyze"}),
    ("regulatory-researcher", "research", "Extracts obligations and open questions from supplied regulatory text.", {"regulatory:analyze"}),
    ("open-source-auditor", "research", "Reviews supplied open-source dependency metadata and license evidence.", {"opensource:audit"}),
    ("data-researcher", "research", "Assesses supplied datasets for provenance, quality, and limitations.", {"data:research"}),
    ("ux-researcher", "research", "Synthesizes supplied usability findings and user pain points.", {"ux:research"}),
    ("product-researcher", "research", "Evaluates product opportunities using supplied requirements and evidence.", {"product:research"}),
    ("ai-researcher", "research", "Compares supplied AI model and architecture documentation.", {"ai:research"}),
    ("cloud-researcher", "research", "Analyzes supplied cloud architecture and service constraints.", {"cloud:research"}),
    ("threat-intelligence-analyst", "research", "Structures supplied threat reports into tactics, indicators, and unknowns.", {"threat:analyze"}),
    ("privacy-researcher", "research", "Identifies privacy implications in supplied data flows and policies.", {"privacy:research"}),
    ("accessibility-researcher", "research", "Reviews supplied interfaces against accessibility requirements.", {"accessibility:research"}),
    ("finance-researcher", "research", "Analyzes supplied financial assumptions and flags missing evidence.", {"finance:research"}),
    ("sales-researcher", "research", "Structures supplied account and market information for sales planning.", {"sales:research"}),
    ("operations-researcher", "research", "Analyzes supplied operational processes and bottlenecks.", {"operations:research"}),
    ("knowledge-curator", "research", "Deduplicates and organizes supplied knowledge into traceable themes.", {"knowledge:curate"}),
    ("fact-checker", "research", "Checks claims against only the supplied evidence and marks unsupported statements.", {"claims:verify"}),
    ("python-engineer", "coding", "Designs Python modules, interfaces, and maintainable implementation plans.", {"python:design"}),
    ("typescript-engineer", "coding", "Designs TypeScript services, types, and application integrations.", {"typescript:design"}),
    ("rust-engineer", "coding", "Designs Rust components with ownership and reliability considerations.", {"rust:design"}),
    ("go-engineer", "coding", "Designs Go services, concurrency patterns, and APIs.", {"go:design"}),
    ("java-engineer", "coding", "Designs Java services and maintainable enterprise integrations.", {"java:design"}),
    ("mobile-engineer", "coding", "Designs mobile application architecture and platform integrations.", {"mobile:design"}),
    ("desktop-engineer", "coding", "Designs cross-platform desktop application components.", {"desktop:design"}),
    ("api-engineer", "coding", "Designs versioned APIs, contracts, and compatibility strategies.", {"api:design"}),
    ("microservices-architect", "coding", "Evaluates service boundaries and distributed-system tradeoffs.", {"microservices:design"}),
    ("event-driven-engineer", "coding", "Designs event-driven workflows and message processing boundaries.", {"events:design"}),
    ("ml-engineer", "coding", "Designs model inference and machine-learning application pipelines.", {"ml:design"}),
    ("llm-engineer", "coding", "Designs LLM orchestration, prompting, and inference integration.", {"llm:design"}),
    ("data-engineer", "coding", "Designs ingestion, transformation, and data quality pipelines.", {"data:engineering"}),
    ("database-architect", "coding", "Designs relational schemas, indexes, and migration strategies.", {"database:architecture"}),
    ("frontend-accessibility-engineer", "coding", "Designs accessible frontend implementation patterns.", {"frontend:accessibility"}),
    ("design-systems-engineer", "coding", "Designs reusable UI tokens, components, and consistency controls.", {"design-system:design"}),
    ("identity-engineer", "coding", "Designs authentication, authorization, and identity integrations.", {"identity:design"}),
    ("observability-engineer", "coding", "Designs logs, metrics, traces, and operational diagnostics.", {"observability:design"}),
    ("reliability-engineer", "coding", "Designs resilience, fault isolation, and recovery strategies.", {"reliability:design"}),
    ("infrastructure-engineer", "coding", "Designs infrastructure-as-code and environment boundaries.", {"infrastructure:design"}),
    ("container-engineer", "coding", "Designs secure container images and runtime configuration.", {"container:design"}),
    ("kubernetes-engineer", "coding", "Designs Kubernetes workloads and deployment controls.", {"kubernetes:design"}),
    ("network-engineer", "coding", "Designs network segmentation, routing, and service connectivity.", {"network:design"}),
    ("incident-response-engineer", "coding", "Creates incident response and remediation implementation plans.", {"incident:design"}),
    ("privacy-engineer", "coding", "Designs data minimization, retention, and privacy-by-design controls.", {"privacy:engineering"}),
    ("cryptography-reviewer", "coding", "Reviews supplied cryptographic designs for misuse and weak assumptions.", {"crypto:review"}),
    ("performance-optimizer", "coding", "Designs profiling plans and targeted performance improvements.", {"performance:optimize"}),
    ("accessibility-engineer", "coding", "Designs implementation changes for accessible interactions.", {"accessibility:engineering"}),
    ("integration-engineer", "coding", "Designs resilient third-party and internal system integrations.", {"integration:engineering"}),
    ("migration-engineer", "coding", "Plans safe application and data migration steps.", {"migration:design"}),
    ("refactoring-engineer", "coding", "Identifies safe refactoring boundaries and incremental modernization steps.", {"refactor:design"}),
    ("code-reviewer", "coding", "Reviews supplied code for correctness, maintainability, and risk.", {"code:review"}),
    ("dependency-maintainer", "coding", "Plans dependency upgrades and compatibility checks.", {"dependency:maintain"}),
    ("release-engineer", "coding", "Designs reproducible build and release automation.", {"release:engineering"}),
    ("cli-engineer", "coding", "Designs command-line interfaces and predictable command behavior.", {"cli:design"}),
    ("desktop-integration-engineer", "coding", "Designs desktop-to-core service integration boundaries.", {"desktop:integration"}),
    ("model-serving-engineer", "coding", "Designs model serving, batching, and inference lifecycle controls.", {"model-serving:design"}),
    ("prompt-engineer", "coding", "Designs structured prompts and prompt evaluation strategies.", {"prompt:design"}),
    ("agent-platform-engineer", "coding", "Designs agent contracts, delegation, and execution boundaries.", {"agent-platform:design"}),
    ("test-automation-engineer", "testing", "Designs maintainable automated test harnesses and fixtures.", {"test:automation"}),
    ("contract-test-engineer", "testing", "Defines provider and service contract compatibility tests.", {"test:contract"}),
    ("end-to-end-test-engineer", "testing", "Designs user-journey and end-to-end acceptance coverage.", {"test:e2e"}),
    ("load-test-engineer", "testing", "Designs load, stress, and capacity test scenarios.", {"test:load"}),
    ("chaos-test-engineer", "testing", "Designs controlled fault-injection and resilience test scenarios.", {"test:chaos"}),
    ("accessibility-test-engineer", "testing", "Designs keyboard, screen-reader, and accessibility test coverage.", {"test:accessibility"}),
    ("privacy-test-engineer", "testing", "Designs data-retention, consent, and privacy regression tests.", {"test:privacy"}),
    ("security-audit-engineer", "testing", "Designs security audit checks and evidence requirements.", {"test:security-audit"}),
    ("migration-test-engineer", "testing", "Designs migration integrity and rollback test scenarios.", {"test:migration"}),
    ("model-evaluation-engineer", "testing", "Designs model quality, regression, and benchmark evaluations.", {"test:model-evaluation"}),
    ("prompt-evaluation-engineer", "testing", "Designs prompt robustness and injection-resistance evaluations.", {"test:prompt-evaluation"}),
    ("data-quality-test-engineer", "testing", "Designs schema, completeness, and anomaly test scenarios.", {"test:data-quality"}),
    ("recovery-test-engineer", "testing", "Designs backup, restore, failover, and recovery validation.", {"test:recovery"}),
    ("cross-platform-test-engineer", "testing", "Designs OS and runtime compatibility test matrices.", {"test:cross-platform"}),
    ("performance-test-engineer", "testing", "Designs latency, throughput, and resource regression tests.", {"test:performance"}),
    ("release-quality-gate", "testing", "Defines objective release gates and required verification evidence.", {"test:release-gate"}),
    ("documentation-qa", "testing", "Checks supplied documentation for accuracy, completeness, and consistency.", {"test:documentation"}),
    ("api-compatibility-tester", "testing", "Designs API backward-compatibility and schema evolution tests.", {"test:api-compatibility"}),
    ("agent-safety-tester", "testing", "Designs agent permission-boundary and unsafe-output tests.", {"test:agent-safety"}),
    ("workflow-test-engineer", "testing", "Designs workflow state, retry, idempotency, and approval tests.", {"test:workflow"}),
)


def starter_agent_specs() -> tuple[AgentSpec, ...]:
    """Return 99 specialists followed by a coordinator allowed to delegate to them."""
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
    """Idempotently seed the 21 profiles; reject conflicting existing definitions."""
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
