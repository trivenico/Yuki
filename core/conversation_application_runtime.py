"""Shared composition and lifecycle for Yuki conversation surfaces.

The API and installed CLI use this runtime so persistence, conversation-service
construction, owner scoping and shutdown semantics do not diverge.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable
import inspect

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
        close = getattr(self.store, "close", None)
        if callable(close):
            close()
        self._closed = True


def build_conversation_application_runtime(
    config: dict[str, Any],
    *,
    agent_loop: AgentExecutionLoop,
    owner_id: str | None = None,
    store_factory: Callable[[dict[str, Any]], Any] = create_conversation_store,
    agent_loop_for_owner: Callable[[str], AgentExecutionLoop] | None = None,
    maintenance_fence=None,
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
    if maintenance_fence is not None and not callable(getattr(maintenance_fence, "lease", None)):
        raise TypeError("maintenance_fence must provide lease()")
    if maintenance_fence is None:
        store = store_factory(config)
    elif store_factory is create_conversation_store:
        store = create_conversation_store(config, maintenance_fence=maintenance_fence)
    else:
        try:
            parameters = inspect.signature(store_factory).parameters.values()
        except (TypeError, ValueError) as exc:
            raise TypeError(
                "A fenced conversation store factory must expose a maintenance_fence parameter."
            ) from exc
        accepts_fence = any(
            parameter.name == "maintenance_fence"
            or parameter.kind is inspect.Parameter.VAR_KEYWORD
            for parameter in parameters
        )
        if not accepts_fence:
            raise TypeError(
                "A fenced conversation store factory must accept maintenance_fence; "
                "refusing an unfenced desktop store."
            )
        store = store_factory(config, maintenance_fence=maintenance_fence)
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
