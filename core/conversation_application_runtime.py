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
    close_callbacks: tuple[Callable[[], None], ...] = ()
    _closed: bool = False

    def close(self) -> None:
        """Close persistence exactly once; safe for repeated shutdown paths."""
        if self._closed:
            return
        # Mark shutdown before invoking user/provider-owned cleanup so a
        # failing close hook is never called twice by nested shutdown paths.
        self._closed = True
        errors: list[Exception] = []
        try:
            self.conversations.close()
        except Exception as exc:
            errors.append(exc)
        close = getattr(self.store, "close", None)
        hooks = ((close,) if callable(close) else ()) + self.close_callbacks
        # The store is created after externally supplied resources, so close it
        # first, then release those resources even if one cleanup hook fails.
        for hook in hooks:
            try:
                hook()
            except Exception as exc:
                errors.append(exc)
        if errors:
            raise errors[0]


def build_conversation_application_runtime(
    config: dict[str, Any],
    *,
    agent_loop: AgentExecutionLoop,
    owner_id: str | None = None,
    store_factory: Callable[[dict[str, Any]], Any] = create_conversation_store,
    agent_loop_for_owner: Callable[[str], AgentExecutionLoop] | None = None,
    close_callbacks: tuple[Callable[[], None], ...] = (),
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
    if agent_loop_for_owner is not None and not callable(agent_loop_for_owner):
        raise TypeError("agent_loop_for_owner must be callable.")
    if not callable(store_factory):
        raise TypeError("store_factory must be callable.")
    if not isinstance(close_callbacks, tuple) or any(
        not callable(callback) for callback in close_callbacks
    ):
        raise TypeError("close_callbacks must be a tuple of callable cleanup hooks.")
    if owner_id is not None and (
        not isinstance(owner_id, str) or not owner_id.strip() or len(owner_id) > 128
    ):
        raise ValueError("owner_id must be non-empty text of at most 128 characters.")
    try:
        store = store_factory(config)
    except Exception:
        for callback in close_callbacks:
            try:
                callback()
            except Exception:
                pass
        raise
    try:
        conversations = ConversationService(
            store, agent_loop, agent_loop_for_owner=agent_loop_for_owner,
        )
        return ConversationApplicationRuntime(
            conversations=conversations, store=store, owner_id=owner_id,
            close_callbacks=close_callbacks,
        )
    except Exception:
        close = getattr(store, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                pass
        for callback in close_callbacks:
            try:
                callback()
            except Exception:
                pass
        raise
