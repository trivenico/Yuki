"""Local CLI composition for durable, owner-scoped assistant conversations.

The CLI deliberately starts with an empty guarded tool registry. Persistent chat
uses the shared conversation application runtime used by the API, but does not
grant the terminal an implicit ability to execute tools. Model providers are
initialized only when a user actually requests model generation.
"""
from __future__ import annotations

import threading
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


class _LazyChatProvider:
    """Initialize an optional provider on first inference, never at CLI startup."""

    def __init__(self, config: dict[str, Any], provider_factory: Callable[..., Any]):
        if not callable(provider_factory):
            raise TypeError("provider_factory must be callable.")
        self._config = dict(config)
        self._provider_factory = provider_factory
        self._provider = None
        self._initialization_failed = False
        self._lock = threading.RLock()

    @property
    def initialized(self) -> bool:
        return self._provider is not None

    def _get_provider(self):
        with self._lock:
            if self._provider is not None:
                return self._provider
            if self._initialization_failed:
                raise RuntimeError("configured_provider_unavailable")
            try:
                provider = self._provider_factory(dict(self._config))
                if provider is None or not callable(getattr(provider, "generate_chat", None)):
                    raise TypeError("provider_does_not_support_chat")
            except Exception:
                # Do not retain provider exception text: it may contain endpoint,
                # account, or credential details. Do not retry on every turn.
                self._initialization_failed = True
                raise RuntimeError("configured_provider_unavailable") from None
            self._provider = provider
            return provider

    def generate_chat(self, *, messages, tools, tool_choice):
        return self._get_provider().generate_chat(
            messages=messages, tools=tools, tool_choice=tool_choice,
        )

    def close(self) -> None:
        with self._lock:
            provider, self._provider = self._provider, None
            close = getattr(provider, "close", None)
            if callable(close):
                close()


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
    """Build persistent local chat without requiring a configured model at startup."""
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

    provider = _LazyChatProvider(config, provider_factory)
    model = ProviderAgentAdapter(provider)
    tool_runtime = GuardedToolRuntime({}, _DenyAllLocalTools())
    protocol = StructuredToolProtocol(tool_runtime)
    # Streaming is intentionally disabled for this lazy adapter. It must not
    # initialize a provider merely to inspect optional streaming capabilities.
    agent_loop = AgentExecutionLoop(
        model,
        protocol,
        stream_model=None,
        max_model_turns=max_turns,
    )
    runtime = build_conversation_application_runtime(
        config, agent_loop=agent_loop, owner_id=owner_id,
        **({"store_factory": store_factory} if store_factory is not None else {}),
    )
    # Keep the lazy adapter owned by this runtime without changing the shared
    # application-runtime interface or its persisted data contracts.
    runtime._provider_adapter = provider
    return runtime
