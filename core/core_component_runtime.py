"""Composition root for Yuki foundational runtime components."""
from __future__ import annotations

from core.agent_component_runtime import build_agent_execution_component_runtime
from core.agent_execution_loop import AgentExecutionLoop
from core.application_component_runtime import build_application_composition_component_runtime
from core.conversation_application_runtime import ConversationApplicationRuntime
from core.durable_conversations_component_runtime import build_durable_conversations_component_runtime
from core.component_registry import ComponentRegistry
from core.identity_component_runtime import build_identity_access_component_runtime
from core.local_identity_component_runtime import build_local_identity_access_component_runtime
from providers.component_runtime import build_provider_component_runtime


def build_core_component_registry(*, auth_service, provider, agent_loop: AgentExecutionLoop, application_runtime: ConversationApplicationRuntime | None = None) -> ComponentRegistry:
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
    if application_runtime is not None:
        registry.register(build_durable_conversations_component_runtime(application_runtime))
        registry.register(build_application_composition_component_runtime(application_runtime))
    return registry

def build_local_core_component_registry(*, owner_id: str, provider, agent_loop: AgentExecutionLoop, application_runtime: ConversationApplicationRuntime) -> ComponentRegistry:
    """Compose the local-only CLI graph with a non-network owner identity."""
    registry = ComponentRegistry()
    runtimes = (
        build_local_identity_access_component_runtime(owner_id),
        build_provider_component_runtime(provider),
        build_agent_execution_component_runtime(agent_loop),
        build_durable_conversations_component_runtime(application_runtime),
        build_application_composition_component_runtime(application_runtime),
    )
    for runtime in runtimes:
        registry.register(runtime)
    return registry
