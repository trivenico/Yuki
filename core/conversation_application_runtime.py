"""Shared composition and lifecycle for Yuki conversation surfaces.

The API and installed CLI use this runtime so persistence, conversation-service
construction, owner scoping and shutdown semantics do not diverge.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from core.agent_execution_loop import AgentExecutionLoop
from core.conversation_service import ConversationService
from core.conversation_store_factory import create_conversation_store


@dataclass
class ConversationApplicationRuntime:
    """Own one conversation service, its store, and its optional local owner scope."""

    conversations: ConversationService
    store: Any
    owner_id: str | None = None
    _closed: bool = False

    @property
    def closed(self) -> bool:
        """Expose lifecycle state without allowing callers to mutate it."""
        return self._closed

    def close(self) -> None:
        """Close persistence exactly once; safe for repeated shutdown paths."""
        if self._closed:
            return
        errors = []
        # Optional adapters owned by a surface runtime are closed before the
        # persistence store. Continue cleanup if either resource raises.
        adapter = getattr(self, "_provider_adapter", None)
        for resource in (adapter, self.store):
            close = getattr(resource, "close", None)
            if callable(close):
                try:
                    close()
                except Exception as exc:
                    errors.append(exc)
        self._closed = True
        if errors:
            raise RuntimeError("conversation runtime could not close cleanly.") from errors[0]


def build_conversation_application_runtime(
    config: dict[str, Any],
    *,
    agent_loop: AgentExecutionLoop,
    owner_id: str | None = None,
    store_factory: Callable[[dict[str, Any]], Any] = create_conversation_store,
    agent_loop_for_owner: Callable[[str], AgentExecutionLoop] | None = None,
) -> ConversationApplicationRuntime:
    """Compose the common durable conversation core for API, CLI and companion.

    Authentication remains a surface responsibility: API owners are resolved
    from authenticated sessions, while a local CLI may supply its local owner
    scope. This function never authenticates a network caller or grants tools.
    """
    if not isinstance(config, dict):
        raise TypeError("config must be a dictionary.")
    if not isinstance(agent_loop, AgentExecutionLoop):
        raise TypeError("agent_loop must be an AgentExecutionLoop.")
    if not callable(store_factory):
        raise TypeError("store_factory must be callable.")
    if owner_id is not None and (
        not isinstance(owner_id, str) or not owner_id.strip() or len(owner_id) > 128
    ):
        raise ValueError("owner_id must be non-empty text of at most 128 characters.")
    store = store_factory(config)
    try:
        conversations = ConversationService(store, agent_loop, agent_loop_for_owner=agent_loop_for_owner)
        return ConversationApplicationRuntime(
            conversations=conversations, store=store, owner_id=owner_id,
        )
    except Exception:
        close = getattr(store, "close", None)
        if callable(close):
            close()
        raise
