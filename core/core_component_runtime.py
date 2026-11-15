"""Composition root for Yuki foundational runtime components."""
from __future__ import annotations

from core.agent_component_runtime import build_agent_execution_component_runtime
from core.agent_execution_loop import AgentExecutionLoop
from core.component_registry import ComponentRegistry
from core.identity_component_runtime import build_identity_access_component_runtime
from providers.component_runtime import build_provider_component_runtime


def build_core_component_registry(*, auth_service, provider, agent_loop: AgentExecutionLoop) -> ComponentRegistry:
    """Register identity, provider and agent runtimes without starting them.

    Registration is separate from startup so callers can inspect or extend
    the complete graph before explicitly invoking start_all().
    """
    registry = ComponentRegistry()
    runtimes = (
        build_identity_access_component_runtime(auth_service),
        build_provider_component_runtime(provider),
        build_agent_execution_component_runtime(agent_loop),
    )
    for runtime in runtimes:
        registry.register(runtime)
    return registry
