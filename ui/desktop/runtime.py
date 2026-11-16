"""Desktop-specific composition of the canonical owner-scoped conversation runtime.

The desktop surface supplies an already authenticated owner ID and a provider
instance. This module adds no UI dependencies and exposes no chat tools.
"""
from __future__ import annotations

from typing import Any, Callable

from core.agent_execution_loop import AgentExecutionLoop
from core.conversation_application_runtime import (
    ConversationApplicationRuntime,
    build_conversation_application_runtime,
)
from core.conversation_store_factory import create_conversation_store
from core.guarded_tool_runtime import GuardedToolRuntime
from core.structured_tool_protocol import StructuredToolProtocol
from providers.agent_adapter import ProviderAgentAdapter


class _DenyAllTools:
    """Desktop chat starts with no executable tools."""

    @staticmethod
    def authorize_tool(name: str) -> bool:
        return False


def build_desktop_conversation_runtime(
    config: dict[str, Any],
    *,
    provider: Any,
    owner_id: str,
    store_factory: Callable[[dict[str, Any]], Any] = create_conversation_store,
    close_provider: bool = True,
) -> ConversationApplicationRuntime:
    """Build the shared runtime for one authenticated desktop owner.

    `owner_id` must come from the trusted desktop session manager's authenticated
    account ID (not a widget field, username, or caller-controlled message).
    By default the runtime owns and closes the provider. Set close_provider=False
    only when an outer desktop lifecycle is the provider's explicit owner.
    """
    if not isinstance(config, dict):
        raise TypeError("config must be a dictionary.")
    if (
        not isinstance(owner_id, str)
        or not owner_id.strip()
        or len(owner_id) > 128
        or any(ord(char) < 32 or ord(char) == 127 for char in owner_id)
    ):
        raise ValueError("owner_id must be non-empty text of at most 128 characters.")
    if type(close_provider) is not bool:
        raise TypeError("close_provider must be a boolean.")

    adapter = ProviderAgentAdapter(provider)
    protocol = StructuredToolProtocol(GuardedToolRuntime({}, _DenyAllTools()))
    stream_model = (
        adapter.stream_model
        if callable(getattr(provider, "generate_chat_stream", None))
        else None
    )
    agent_loop = AgentExecutionLoop(
        adapter,
        protocol,
        stream_model=stream_model,
        max_model_turns=8,
        max_tool_calls=1,
        max_duration_seconds=120,
    )
    close = getattr(provider, "close", None)
    callbacks = (close,) if close_provider and callable(close) else ()
    return build_conversation_application_runtime(
        config,
        agent_loop=agent_loop,
        owner_id=owner_id.strip(),
        store_factory=store_factory,
        close_callbacks=callbacks,
    )
