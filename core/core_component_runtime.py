"""Composition root for Yuki foundational runtime components."""
from __future__ import annotations

from core.agent_component_runtime import build_agent_execution_component_runtime
from core.agent_execution_loop import AgentExecutionLoop
from core.application_component_runtime import build_application_composition_component_runtime
from core.configuration_component_runtime import build_configuration_component_runtime
from core.conversation_application_runtime import ConversationApplicationRuntime
from core.durable_conversations_component_runtime import build_durable_conversations_component_runtime
from core.component_registry import ComponentRegistry
from core.identity_component_runtime import build_identity_access_component_runtime
from core.local_identity_component_runtime import build_local_identity_access_component_runtime
from core.network_firewall import NetworkFirewall, build_network_firewall_from_config
from core.network_firewall_component_runtime import build_network_firewall_component_runtime
from core.owner_agent_component_runtime import build_owner_agent_execution_component_runtime
from providers.component_runtime import build_provider_component_runtime


def _register_configuration(registry: ComponentRegistry, config: dict | None) -> None:
    if config is not None:
        registry.register(build_configuration_component_runtime(config))


def build_core_component_registry(
    *,
    auth_service,
    provider,
    agent_loop: AgentExecutionLoop,
    application_runtime: ConversationApplicationRuntime | None = None,
    config: dict | None = None,
    network_firewall: NetworkFirewall | None = None,
) -> ComponentRegistry:
    """Register validated configuration and foundational API component runtimes."""
    registry = ComponentRegistry()
    _register_configuration(registry, config)
    if network_firewall is None and config is not None:
        network_firewall = build_network_firewall_from_config(config)
    runtimes = (
        build_identity_access_component_runtime(auth_service),
        build_provider_component_runtime(provider),
        build_agent_execution_component_runtime(agent_loop),
    )
    for runtime in runtimes:
        registry.register(runtime)
    if network_firewall is not None:
        registry.register(build_network_firewall_component_runtime(network_firewall))
    if application_runtime is not None:
        owner_factory = application_runtime.conversations.agent_loop_for_owner
        if owner_factory is not None:
            registry.register(build_owner_agent_execution_component_runtime(owner_factory))
        registry.register(build_durable_conversations_component_runtime(application_runtime))
        registry.register(build_application_composition_component_runtime(application_runtime))
    return registry


def build_local_core_component_registry(
    *,
    owner_id: str,
    provider,
    agent_loop: AgentExecutionLoop,
    application_runtime: ConversationApplicationRuntime,
    config: dict | None = None,
    network_firewall: NetworkFirewall | None = None,
) -> ComponentRegistry:
    """Compose the local-only CLI graph with configuration and local identity."""
    registry = ComponentRegistry()
    _register_configuration(registry, config)
    if network_firewall is None and config is not None:
        network_firewall = build_network_firewall_from_config(config)
    runtimes = (
        build_local_identity_access_component_runtime(owner_id),
        build_provider_component_runtime(provider),
        build_agent_execution_component_runtime(agent_loop),
    )
    for runtime in runtimes:
        registry.register(runtime)
    if network_firewall is not None:
        registry.register(build_network_firewall_component_runtime(network_firewall))
    owner_factory = application_runtime.conversations.agent_loop_for_owner
    if owner_factory is not None:
        registry.register(build_owner_agent_execution_component_runtime(owner_factory))
    runtimes = (
        build_durable_conversations_component_runtime(application_runtime),
        build_application_composition_component_runtime(application_runtime),
    )
    for runtime in runtimes:
        registry.register(runtime)
    return registry
