"""Canonical architecture catalog for Yuki's major runtime components.

This catalog describes integration boundaries, not feature-completion claims.
A component is only marked implemented when its end-to-end contract is backed
by tests; experimental components must retain their limitations in this map.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable
import json


COMPONENT_STATUSES = frozenset({"implemented", "experimental", "planned"})
LAYERS = frozenset({
    "interface", "application", "orchestration", "domain", "infrastructure",
    "security", "integration",
})


@dataclass(frozen=True)
class ComponentContract:
    component_id: str
    name: str
    layer: str
    status: str
    purpose: str
    entrypoints: tuple[str, ...]
    depends_on: tuple[str, ...]
    public_contract: tuple[str, ...]
    test_evidence: tuple[str, ...]
    limitations: tuple[str, ...] = ()

    def validate(self) -> list[str]:
        errors: list[str] = []
        if not isinstance(self.component_id, str) or not self.component_id or not self.component_id.replace("-", "").replace("_", "").isalnum():
            errors.append("component_id must be a non-empty simple identifier")
        if self.layer not in LAYERS:
            errors.append(f"unsupported layer: {self.layer}")
        if self.status not in COMPONENT_STATUSES:
            errors.append(f"unsupported status: {self.status}")
        if not isinstance(self.name, str) or not self.name.strip():
            errors.append("name is required")
        if not isinstance(self.purpose, str) or not self.purpose.strip():
            errors.append("purpose is required")
        if not self.entrypoints:
            errors.append("at least one entrypoint is required")
        if not self.public_contract:
            errors.append("at least one public contract is required")
        if self.status == "implemented" and not self.test_evidence:
            errors.append("implemented components require test evidence")
        fields = (
            self.entrypoints + self.depends_on + self.public_contract
            + self.test_evidence + self.limitations
        )
        if any(not isinstance(value, str) or not value.strip() for value in fields):
            errors.append("contract collections must contain non-empty strings")
        if len(set(self.entrypoints)) != len(self.entrypoints):
            errors.append("duplicate entrypoint")
        if len(set(self.depends_on)) != len(self.depends_on):
            errors.append("duplicate dependency")
        return errors


# Dependencies are component IDs, not package names. Keep this graph acyclic:
# interfaces depend inward on application/domain contracts, never the reverse.
DEFAULT_COMPONENTS: tuple[ComponentContract, ...] = (
    ComponentContract(
        "identity-access", "Identity and access control", "security", "experimental",
        "Resolves local/authenticated principals and centralizes permission decisions.",
        ("core/auth.py", "core/local_owner_identity.py", "core/permissions.py"),
        (), ("principal resolution", "role/permission checks", "session lifecycle"),
        ("tests/test_auth.py",),
        ("Hosted HTTP authentication middleware, MFA and account recovery are not complete.",),
    ),
    ComponentContract(
        "provider-runtime", "Model provider runtime", "infrastructure", "experimental",
        "Constructs configured model adapters and exposes provider/model selection.",
        ("providers/factory.py", "providers/runtime_manager.py"),
        (), ("provider adapter contract", "model selection", "normalized inference errors"),
        ("tests/test_provider_factory.py", "tests/test_provider_runtime_manager.py"),
        ("Adapter availability does not establish production latency, quality or failover.",),
    ),
    ComponentContract(
        "agent-execution", "Agent execution loop", "orchestration", "experimental",
        "Runs bounded model-guided turns and routes explicitly authorized skill/tool work.",
        ("core/agent_execution_loop.py", "core/agent.py", "core/task_agent.py"),
        ("provider-runtime", "identity-access"),
        ("bounded turn execution", "authorized action dispatch", "cancellation and budgets"),
        ("tests/test_yuki_agent_runtime.py",),
        ("Native general-purpose tool-calling and complete multi-agent autonomy are not established.",),
    ),
    ComponentContract(
        "durable-conversations", "Durable conversation domain", "domain", "experimental",
        "Owns conversation lifecycle, persistence, execution identity and recovery state.",
        ("core/conversation_service.py", "core/conversation_store.py",
         "core/conversation_store_factory.py"),
        ("identity-access",),
        ("owner-scoped conversation operations", "durable execution journal",
         "interruption and recovery semantics"),
        ("tests/test_conversation_service.py", "tests/test_conversation_store.py"),
        ("Hosted multi-region persistence and operational recovery are not established.",),
    ),
    ComponentContract(
        "application-composition", "Shared application composition", "application", "experimental",
        "Composes the shared conversation service and store for API and CLI surfaces.",
        ("core/conversation_application_runtime.py", "api/runtime.py"),
        ("agent-execution", "durable-conversations"),
        ("single shared service construction", "deterministic lifecycle and shutdown"),
        ("tests/test_api_runtime.py",),
        ("The composition layer does not itself provide network authentication or deployment.",),
    ),
    ComponentContract(
        "guarded-actions", "Guarded tools and approval boundary", "security", "experimental",
        "Applies action policy and approval requirements before consequential tool effects.",
        ("core/action_approvals.py", "core/guarded_tool_runtime.py",
         "core/permissions.py"),
        ("identity-access",),
        ("permission evaluation", "approval decision", "auditable action execution"),
        ("tests/test_workflow_approval.py", "tests/test_workflow_store.py"),
        ("Each tool integration still needs its own threat model and end-to-end tests.",),
    ),
    ComponentContract(
        "durable-workflows", "Durable workflow and specialist orchestration", "orchestration", "experimental",
        "Plans bounded tasks, persists task state and dispatches authorized specialist work.",
        ("automation/agent_coordinator.py", "automation/task_runtime.py",
         "automation/workflow_engine.py"),
        ("agent-execution", "guarded-actions"),
        ("durable task lifecycle", "bounded specialist dispatch", "approval-gated workflow steps"),
        ("tests/test_agent_coordinator.py", "tests/test_task_runtime.py",
         "tests/test_workflow_engine.py"),
        ("A production scheduler, distributed worker fleet and cancellation propagation remain incomplete.",),
    ),
    ComponentContract(
        "memory", "Memory and retrieval", "domain", "experimental",
        "Stores user-approved local notes and provides lexical retrieval primitives.",
        ("core/vector_memory.py",),
        ("identity-access",),
        ("owner-scoped memory read/write/delete", "retrieval result contract"),
        ("tests/test_yuki.py",),
        ("This is not yet a tenant-aware hosted vector database or verified semantic memory service.",),
    ),
    ComponentContract(
        "business-integrations", "Business and income integrations", "integration", "experimental",
        "Tracks income opportunities and describes reviewed platform capabilities.",
        ("core/income_discovery.py", "core/income_opportunities.py",
         "core/platform_registry.py"),
        ("identity-access", "guarded-actions", "durable-workflows"),
        ("owner-scoped opportunity lifecycle", "platform capability policy",
         "approval-gated external actions"),
        ("tests/test_income_discovery.py", "tests/test_income_opportunities.py",
         "tests/test_income_api.py"),
        ("No live platform adapter or verified revenue generation is implied by this component.",),
    ),
    ComponentContract(
        "conversation-api", "Conversation HTTP interface", "interface", "experimental",
        "Exposes conversation operations through the shared application/service layer.",
        ("api/conversations.py",),
        ("application-composition", "identity-access", "guarded-actions"),
        ("authenticated conversation endpoints", "request validation", "owner isolation"),
        ("tests/test_conversation_api.py",),
        ("A production hosted deployment and complete API threat review remain outstanding.",),
    ),
)


def component_catalog() -> tuple[ComponentContract, ...]:
    """Return the immutable canonical component catalog."""
    return DEFAULT_COMPONENTS


def validate_component_catalog(
    components: Iterable[ComponentContract] | None = None,
    repository_root: str | Path = ".",
) -> list[str]:
    """Validate contracts, references, evidence files and dependency acyclicity."""
    root = Path(repository_root).resolve()
    entries = tuple(DEFAULT_COMPONENTS if components is None else components)
    errors: list[str] = []
    by_id: dict[str, ComponentContract] = {}

    for item in entries:
        prefix = item.component_id or "<missing-id>"
        for error in item.validate():
            errors.append(f"{prefix}: {error}")
        if item.component_id in by_id:
            errors.append(f"{prefix}: duplicate component_id")
        by_id[item.component_id] = item

        for relative in item.entrypoints + item.test_evidence:
            path = Path(relative)
            if path.is_absolute() or ".." in path.parts:
                errors.append(f"{prefix}: unsafe repository path {relative!r}")
                continue
            if not (root / path).is_file():
                errors.append(f"{prefix}: repository file does not exist: {relative}")

    for item in entries:
        for dependency in item.depends_on:
            if dependency not in by_id:
                errors.append(f"{item.component_id}: unknown dependency {dependency}")

    # Three-colour DFS reports cycles without relying on declaration order.
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(component_id: str) -> None:
        if component_id in visited or component_id not in by_id:
            return
        if component_id in visiting:
            errors.append(f"{component_id}: dependency cycle detected")
            return
        visiting.add(component_id)
        for dependency in by_id[component_id].depends_on:
            visit(dependency)
        visiting.remove(component_id)
        visited.add(component_id)

    for component_id in by_id:
        visit(component_id)
    return errors


def component_catalog_as_dicts() -> list[dict]:
    """Return JSON-safe component contracts for docs, CLI and future UI."""
    return [asdict(item) for item in DEFAULT_COMPONENTS]


def component_catalog_json() -> str:
    return json.dumps(component_catalog_as_dicts(), ensure_ascii=False, indent=2)
