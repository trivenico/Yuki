"""Build workers only from explicitly enabled, reviewed specialist registrations.

The catalog is descriptive metadata, not an executable plugin system. This
factory requires an explicit agent-to-role mapping and verifies that every
selected agent advertises at least one stable catalog capability ID.
"""
from __future__ import annotations

from typing import Mapping

from automation.agent_coordinator import SpecialistAgentCoordinator
from automation.capability_catalog import CAPABILITIES
from automation.specialist_worker import (
    SpecialistTaskWorker,
    build_handlers_from_provider_manager,
)
from automation.task_runtime import TaskRuntime
from core.gate_first_sandbox_adapter import GateFirstSandboxExecutorAdapter
from core.gate_first_sandbox_executor import GateFirstDockerSandboxExecutor
from core.sandbox_executor import SandboxExecutionProfile

_SUPPORTED_CATALOG_IDS = frozenset(item.capability_id for item in CAPABILITIES)


def build_registered_specialist_worker(
    *,
    coordinator: SpecialistAgentCoordinator,
    runtime: TaskRuntime,
    actor_id: str,
    worker_id: str,
    service_actor_id: str,
    provider_manager,
    agent_roles: Mapping[str, str],
    provider_preferences: Mapping[str, tuple[str, ...]] | None = None,
    allow_cloud: bool = False,
    sandbox_executor=None,
    sandbox_profiles: Mapping[str, object] | None = None,
) -> SpecialistTaskWorker:
    """Create a worker from enabled registry entries and explicit role mappings.

    This does not register agents, grant permissions, approve tasks, or start a
    worker. The caller must separately invoke run_once/run_concurrently.
    """
    if not isinstance(coordinator, SpecialistAgentCoordinator):
        raise TypeError("coordinator must be a SpecialistAgentCoordinator.")
    if not isinstance(runtime, TaskRuntime):
        raise TypeError("runtime must be a TaskRuntime.")
    if coordinator.runtime is not runtime:
        raise ValueError("coordinator and worker factory must share the same task runtime.")
    runtime.assert_worker_service_permissions(service_actor_id)
    if not isinstance(agent_roles, Mapping) or not agent_roles or len(agent_roles) > 32:
        raise ValueError("agent_roles must be a non-empty bounded mapping.")

    registry = coordinator.list_agents(actor_id=actor_id)
    by_id = {item["agent_id"]: item for item in registry}
    for agent_id in agent_roles:
        registered = by_id.get(agent_id)
        if registered is None:
            raise PermissionError("worker mapping references an unregistered agent.")
        if registered["enabled"] is not True:
            raise PermissionError("disabled specialists cannot be wired to a worker.")
        if registered.get("action") != "emit":
            raise ValueError("only emit-mode specialists can use the text-only worker.")
        if not (_SUPPORTED_CATALOG_IDS & set(registered["capabilities"])):
            raise ValueError("worker agents must advertise a stable catalog capability ID.")

    handlers = build_handlers_from_provider_manager(
        provider_manager,
        agent_roles,
        provider_preferences=provider_preferences,
        allow_cloud=allow_cloud,
    )
    return SpecialistTaskWorker(
        runtime,
        worker_id=worker_id,
        service_actor_id=service_actor_id,
        handlers=handlers,
        handler_capabilities={
            agent_id: tuple(sorted(by_id[agent_id]["capabilities"]))
            for agent_id in agent_roles
        },
        sandbox_executor=sandbox_executor,
        sandbox_profiles=sandbox_profiles,
    )



def build_gate_first_registered_specialist_worker(
    *,
    gate_first_executor: GateFirstDockerSandboxExecutor,
    sandbox_profiles: Mapping[str, SandboxExecutionProfile],
    **worker_options,
) -> SpecialistTaskWorker:
    """Build a registered specialist worker that cannot omit the gate-first adapter."""
    if not isinstance(gate_first_executor, GateFirstDockerSandboxExecutor):
        raise TypeError("gate_first_executor must be a GateFirstDockerSandboxExecutor.")
    if not isinstance(sandbox_profiles, Mapping) or not sandbox_profiles:
        raise ValueError("gate-first workers require a non-empty sandbox profile mapping.")
    if len(sandbox_profiles) > 64:
        raise ValueError("sandbox profile mapping exceeds the supported bound.")
    validated = {}
    for spec_id, profile in sandbox_profiles.items():
        if (
            not isinstance(spec_id, str) or not spec_id
            or not isinstance(profile, SandboxExecutionProfile)
            or profile.spec_id != spec_id
        ):
            raise ValueError("gate-first sandbox profiles must be keyed by their exact spec_id.")
        validated[spec_id] = profile
    if "sandbox_executor" in worker_options or "sandbox_profiles" in worker_options:
        raise ValueError("gate-first executor and profiles must use the dedicated factory arguments.")
    return build_registered_specialist_worker(
        **worker_options,
        sandbox_executor=GateFirstSandboxExecutorAdapter(gate_first_executor),
        sandbox_profiles=validated,
    )
