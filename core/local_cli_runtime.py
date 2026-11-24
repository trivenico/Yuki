"""Local CLI composition for durable, owner-scoped assistant conversations.

The CLI deliberately starts with an empty guarded tool registry. Persistent chat
uses the shared conversation application runtime used by the API, but does not
grant the terminal an implicit ability to execute tools.
"""
from __future__ import annotations

from typing import Any, Callable

from core.agent_execution_loop import AgentExecutionLoop
from core.conversation_application_runtime import (
    ConversationApplicationRuntime,
    build_conversation_application_runtime,
)
from core.guarded_tool_runtime import GuardedToolRuntime
from core.local_owner_identity import load_or_create_local_owner_id
from core.structured_tool_protocol import StructuredToolProtocol
from providers.agent_adapter import ProviderAgentAdapter
from providers.factory import create_provider
from core.production_security import ProductionSecurityContext
from providers.authorized_agent_adapter import AuthorizedProviderAgentAdapter


class _DenyAllLocalTools:
    """No CLI tool can run until an explicit, separately reviewed registry exists."""

    def authorize_tool(self, name: str) -> bool:
        return False


# Preserve the established import name while sharing the actual runtime type.
LocalConversationRuntime = ConversationApplicationRuntime


def build_local_conversation_runtime(
    config: dict[str, Any],
    *,
    provider_factory: Callable[..., Any] = create_provider,
    store_factory: Callable[[dict[str, Any]], Any] | None = None,
    owner_id_factory: Callable[[], str] | None = None,
    security: ProductionSecurityContext | None = None,
    require_central_authorization: bool = False,
) -> LocalConversationRuntime:
    """Build the installed CLI's persistent chat service with fail-closed tools."""
    if not isinstance(config, dict):
        raise TypeError("config must be a dictionary.")
    if type(require_central_authorization) is not bool:
        raise TypeError("require_central_authorization must be a boolean.")
    if security is not None and not isinstance(security, ProductionSecurityContext):
        raise TypeError("security must be a ProductionSecurityContext.")
    if require_central_authorization and security is None:
        raise ValueError("production_cli_requires_security_context")
    # Validate all caller-controlled runtime settings before constructing a
    # provider or touching persistent identity/storage resources.
    max_turns = config.get("max_agent_steps", 8)
    if type(max_turns) is not int or not 1 <= max_turns <= 100:
        raise ValueError("max_agent_steps must be an integer from 1 to 100.")
    owner_id = (
        owner_id_factory() if owner_id_factory is not None
        else load_or_create_local_owner_id(config.get("workspace"))
    )
    if not isinstance(owner_id, str) or not owner_id.strip() or len(owner_id) > 128:
        raise ValueError("local owner identity is invalid.")

    provider = provider_factory(config)
    if require_central_authorization:
        principal = security.require_actor(owner_id)
        model = AuthorizedProviderAgentAdapter(
            provider, authorization_gateway=security.authorization_gateway,
            principal=principal, policy_revision=security.policy_revision,
        )
    else:
        model = ProviderAgentAdapter(provider)
    tool_runtime = GuardedToolRuntime({}, _DenyAllLocalTools())
    protocol = StructuredToolProtocol(tool_runtime)
    agent_loop = AgentExecutionLoop(
        model,
        protocol,
        stream_model=(
            model.stream_model
            if callable(getattr(provider, "generate_chat_stream", None)) else None
        ),
        max_model_turns=max_turns,
    )
    return build_conversation_application_runtime(
        config, agent_loop=agent_loop, owner_id=owner_id,
        **({"store_factory": store_factory} if store_factory is not None else {}),
    )
