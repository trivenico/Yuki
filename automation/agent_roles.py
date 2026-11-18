"""Versioned, non-dispatchable starter role manifests for Yuki's AI company.

These manifests describe intended specialist responsibilities. They are disabled
until a concrete, reviewed handler is implemented and explicitly activated.
"""
from __future__ import annotations

from automation.agent_coordinator import SpecialistAgent, SpecialistAgentCoordinator

ROLE_MANIFEST_VERSION = 1

# Each role starts with a narrow capability and no permission grants. The current
# specialist worker only supports the bounded "emit" action; these slots therefore
# remain handler-not-ready and cannot be selected for dispatch.
INITIAL_ROLE_SLOTS = (
    {
        "agent_id": "EXEC-01",
        "name": "Chief of Staff",
        "department": "Executive Office",
        "capabilities": frozenset({"operating_plan", "task_coordination"}),
        "owner": "Yuki CEO",
        "handler_id": None,
        "max_steps": 5,
        "max_tokens": 4000,
        "approval_class": "review_required",
    },
    {
        "agent_id": "RES-12",
        "name": "Research Analyst",
        "department": "Research & Frontier AI",
        "capabilities": frozenset({"research_synthesis", "source_citation"}),
        "owner": "Chief Research Officer",
        "handler_id": None,
        "max_steps": 5,
        "max_tokens": 6000,
        "approval_class": "read_only",
    },
    {
        "agent_id": "ENG-03",
        "name": "Software Engineer",
        "department": "Engineering & Infrastructure",
        "capabilities": frozenset({"code_review", "test_analysis"}),
        "owner": "CTO",
        "handler_id": None,
        "max_steps": 8,
        "max_tokens": 8000,
        "approval_class": "review_required",
    },
    {
        "agent_id": "ENG-16",
        "name": "QA / Evals Engineer",
        "department": "Engineering & Infrastructure",
        "capabilities": frozenset({"test_design", "quality_evaluation"}),
        "owner": "CTO",
        "handler_id": None,
        "max_steps": 8,
        "max_tokens": 8000,
        "approval_class": "read_only",
    },
    {
        "agent_id": "SEC-02",
        "name": "Security Reviewer",
        "department": "Security, Safety & Privacy",
        "capabilities": frozenset({"security_review", "threat_analysis"}),
        "owner": "CISO",
        "handler_id": None,
        "max_steps": 6,
        "max_tokens": 6000,
        "approval_class": "read_only",
    },
    {
        "agent_id": "PROD-09",
        "name": "Technical Writer",
        "department": "Product & Documentation",
        "capabilities": frozenset({"technical_documentation", "runbook_authoring"}),
        "owner": "Chief Product Officer",
        "handler_id": None,
        "max_steps": 6,
        "max_tokens": 6000,
        "approval_class": "review_required",
    },
)



# These four existing provider-backed prompt handlers have implementations in
# automation.specialist_worker. They are available as adapters, but role slots
# remain disabled until deployment wiring, permissions, and review are complete.
INITIAL_PROVIDER_ROLE_MAP = {
    "RES-12": "research",
    "ENG-03": "coding",
    "ENG-16": "testing",
    "PROD-09": "reporting",
}


def build_initial_role_handlers(provider_manager, *, allow_cloud: bool = False):
    """Build provider-backed handlers for the four implemented text-only roles.

    Returns the exact mapping expected by SpecialistTaskWorker. This function
    does not register, enable, or mark any role handler-ready.
    """
    from automation.specialist_worker import build_handlers_from_provider_manager

    return build_handlers_from_provider_manager(
        provider_manager,
        INITIAL_PROVIDER_ROLE_MAP,
        allow_cloud=allow_cloud,
    )

def build_initial_role_worker(
    runtime,
    coordinator: SpecialistAgentCoordinator,
    provider_manager,
    *,
    worker_id: str,
    service_actor_id: str,
    allow_cloud: bool = False,
):
    """Construct the initial text-only worker with mandatory manifest validation.

    This is the supported composition path for the starter role handlers. It
    deliberately does not register, enable, or mark roles ready. Runtime task
    authorization remains enforced by TaskRuntime; the coordinator validator
    is always attached so manifest-bound tasks fail closed.
    """
    from automation.specialist_worker import SpecialistTaskWorker
    from automation.task_runtime import TaskRuntime

    if not isinstance(runtime, TaskRuntime):
        raise TypeError("runtime must be a TaskRuntime.")
    if not isinstance(coordinator, SpecialistAgentCoordinator):
        raise TypeError("coordinator must be a SpecialistAgentCoordinator.")
    if coordinator.runtime is not runtime:
        raise ValueError("coordinator and worker must share the same TaskRuntime.")
    handlers = build_initial_role_handlers(provider_manager, allow_cloud=allow_cloud)
    return SpecialistTaskWorker(
        runtime,
        worker_id=worker_id,
        service_actor_id=service_actor_id,
        handlers=handlers,
        manifest_validator=coordinator.validate_task_manifest,
    )


def build_initial_role_slots() -> tuple[SpecialistAgent, ...]:
    """Build validated role slots; none is executable or enabled by default."""
    return tuple(
        SpecialistAgent(
            agent_id=item["agent_id"],
            name=item["name"],
            capabilities=item["capabilities"],
            action="emit",
            permissions=frozenset(),
            enabled=False,
            department=item["department"],
            role_version=ROLE_MANIFEST_VERSION,
            owner=item["owner"],
            handler_id=item["handler_id"],
            handler_ready=False,
            max_steps=item["max_steps"],
            max_tokens=item["max_tokens"],
            approval_class=item["approval_class"],
        )
        for item in INITIAL_ROLE_SLOTS
    )


def register_initial_role_slots(
    coordinator: SpecialistAgentCoordinator, *, actor_id: str,
) -> tuple[str, ...]:
    """Persist the six disabled role manifests after coordinator authorization."""
    if not isinstance(coordinator, SpecialistAgentCoordinator):
        raise TypeError("coordinator must be a SpecialistAgentCoordinator.")
    registered: list[str] = []
    for specialist in build_initial_role_slots():
        coordinator.register_agent(actor_id=actor_id, agent=specialist)
        registered.append(specialist.agent_id)
    return tuple(registered)
