"""Authenticated desktop composition for the durable conversation runtime.

The returned session is one lifecycle resource. It owns the conversation store
and facade, but deliberately does not own the provider; the desktop process
lifecycle must close the provider exactly once.
"""
from __future__ import annotations

from threading import RLock
from typing import Any, Callable

from core.conversation_application_runtime import ConversationApplicationRuntime
from core.conversation_store_factory import create_conversation_store
from ui.desktop.authenticated_conversation import AuthenticatedDesktopConversation
from ui.desktop.runtime import build_desktop_conversation_runtime


class DesktopConversationSession:
    """Own one authenticated facade and its durable runtime as a closeable unit."""

    def __init__(
        self,
        runtime: ConversationApplicationRuntime,
        conversation: AuthenticatedDesktopConversation,
    ) -> None:
        self.runtime = runtime
        self.conversation = conversation
        self._closed = False
        self._close_lock = RLock()

    def close(self) -> None:
        """Invalidate the UI session before closing its durable conversation store."""
        with self._close_lock:
            if self._closed:
                return
            self._closed = True
            errors: list[Exception] = []
            try:
                self.conversation.invalidate()
            except Exception as exc:
                errors.append(exc)
            try:
                self.runtime.close()
            except Exception as exc:
                errors.append(exc)
            if errors:
                raise RuntimeError(
                    "Desktop conversation shutdown could not complete every cleanup step."
                ) from errors[0]


def build_authenticated_desktop_conversation(
    config: dict[str, Any],
    *,
    provider: Any,
    identity: Any,
    sessions: Any,
    memory: Any = None,
    learning: Any = None,
    store_factory: Callable[[dict[str, Any]], Any] = create_conversation_store,
) -> DesktopConversationSession:
    """Compose a live-session-checked durable chat for one authenticated owner.

    The owner is taken only from identity.actor_id and verified by the session
    manager before the runtime is created. Provider shutdown remains with the
    outer desktop lifecycle, so callers must register this returned session and
    provider as separate, uniquely owned resources. An optional learning session
    is owned by the authenticated facade and invalidated during this session's close.
    """
    actor_id = getattr(identity, "actor_id", None)
    if not isinstance(actor_id, str) or not actor_id.strip():
        raise ValueError("identity must contain an authenticated actor_id.")
    if not callable(getattr(sessions, "current_identity", None)):
        raise TypeError("sessions must provide current_identity().")
    if sessions.current_identity() != identity:
        raise PermissionError("The desktop session is no longer authenticated.")

    runtime = build_desktop_conversation_runtime(
        config,
        provider=provider,
        owner_id=actor_id,
        store_factory=store_factory,
    )
    try:
        facade = AuthenticatedDesktopConversation(
            runtime, identity=identity, sessions=sessions, memory=memory,
            learning=learning,
        )
        return DesktopConversationSession(runtime, facade)
    except Exception:
        runtime.close()
        raise
