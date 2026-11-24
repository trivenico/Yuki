"""Local CLI composition for durable, owner-scoped assistant conversations.

The CLI deliberately starts with an empty guarded tool registry. Persistent chat
uses the shared conversation application runtime used by the API, but does not
grant the terminal an implicit ability to execute tools.
"""
from __future__ import annotations

from typing import Any, Callable

from core.agent_execution_loop import AgentExecutionLoop
from core.authorization import Principal
from core.security_composition import build_authorization_security_runtime
from core.conversation_application_runtime import (
    ConversationApplicationRuntime,
    build_conversation_application_runtime,
)
from core.guarded_tool_runtime import GuardedToolRuntime
from core.local_owner_identity import load_or_create_local_owner_id
from core.structured_tool_protocol import StructuredToolProtocol
from providers.authorized_agent_adapter import AuthorizedProviderAgentAdapter
from providers.factory import create_provider


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
) -> LocalConversationRuntime:
    """Build the installed CLI's persistent chat service with fail-closed tools."""
    if not isinstance(config, dict):
        raise TypeError("config must be a dictionary.")
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

    workspace = Path(config.get("workspace") or (Path.home() / ".yuki")).expanduser()
    policy_revision = config.get("authorization_policy_revision", "local-v1")
    security = build_authorization_security_runtime(
        workspace / "authorization-audit.sqlite3",
        policy_revision=policy_revision,
    )
    principal = Principal(
        principal_id=owner_id,
        kind="human",
        permissions=frozenset({"inference:invoke"}),
    )
    provider = provider_factory(config)
    model = AuthorizedProviderAgentAdapter(
        provider,
        authorization_gateway=security.gateway,
        principal=principal,
        provider_name=str(config.get("provider", type(provider).__name__)).lower(),
        policy_revision=security.policy.policy_revision,
    )
    tool_runtime = GuardedToolRuntime(
        {}, security.gateway, principal=principal,
        policy_revision=security.policy.policy_revision,
    )
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
        agent_loop_for_owner=lambda requested_owner: (
            agent_loop if requested_owner == owner_id
            else (_raise_owner_mismatch())
        ),
        **({"store_factory": store_factory} if store_factory is not None else {}),
    )


def _raise_owner_mismatch():
    raise PermissionError("local_conversation_owner_mismatch")
