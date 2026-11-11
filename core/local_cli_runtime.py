"""Local CLI composition for durable, owner-scoped assistant conversations.

The CLI deliberately starts with an empty guarded tool registry. Persistent chat
uses the same provider-neutral agent loop and conversation service as the API,
but does not grant the terminal an implicit ability to execute tools.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from core.agent_execution_loop import AgentExecutionLoop
from core.conversation_service import ConversationService
from core.conversation_store_factory import create_conversation_store
from core.guarded_tool_runtime import GuardedToolRuntime
from core.local_owner_identity import load_or_create_local_owner_id
from core.structured_tool_protocol import StructuredToolProtocol
from providers.agent_adapter import ProviderAgentAdapter
from providers.factory import create_provider


class _DenyAllLocalTools:
    """No CLI tool can run until an explicit, separately reviewed registry exists."""

    def authorize_tool(self, name: str) -> bool:
        return False


@dataclass
class LocalConversationRuntime:
    owner_id: str
    conversations: ConversationService
    store: Any

    def close(self) -> None:
        close = getattr(self.store, "close", None)
        if callable(close):
            close()


def build_local_conversation_runtime(
    config: dict[str, Any],
    *,
    provider_factory: Callable[..., Any] = create_provider,
    store_factory: Callable[[dict[str, Any]], Any] = create_conversation_store,
    owner_id_factory: Callable[[], str] | None = None,
) -> LocalConversationRuntime:
    """Build the installed CLI's persistent chat service with fail-closed tools."""
    if not isinstance(config, dict):
        raise TypeError("config must be a dictionary.")
    provider = provider_factory(config)
    model = ProviderAgentAdapter(provider)
    tool_runtime = GuardedToolRuntime({}, _DenyAllLocalTools())
    protocol = StructuredToolProtocol(tool_runtime)
    max_turns = config.get("max_agent_steps", 8)
    if type(max_turns) is not int or not 1 <= max_turns <= 100:
        raise ValueError("max_agent_steps must be an integer from 1 to 100.")
    agent_loop = AgentExecutionLoop(
        model,
        protocol,
        stream_model=getattr(model, "stream_model", None)
        if callable(getattr(provider, "generate_chat_stream", None)) else None,
        max_model_turns=max_turns,
    )
    store = store_factory(config)
    try:
        owner_id = (owner_id_factory() if owner_id_factory is not None else\n                    load_or_create_local_owner_id(config.get("workspace")))
        if not isinstance(owner_id, str) or not owner_id.strip() or len(owner_id) > 128:
            raise ValueError("local owner identity is invalid.")
        return LocalConversationRuntime(
            owner_id=owner_id,
            conversations=ConversationService(store, agent_loop),
            store=store,
        )
    except Exception:
        close = getattr(store, "close", None)
        if callable(close):
            close()
        raise
