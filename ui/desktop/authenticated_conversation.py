"""Bind the shared desktop conversation adapter to a live authenticated session.

Authentication remains owned by the desktop session manager. This wrapper
rechecks that session at every operation and never accepts an owner ID from UI
input. Runtime shutdown remains the responsibility of the application lifecycle.
"""
from __future__ import annotations

from typing import Any, Callable

from ui.desktop.conversation_adapter import DesktopConversationAdapter


class AuthenticatedDesktopConversation:
    """Session-checked facade for one authenticated desktop account."""

    def __init__(self, application: Any, *, identity: Any, sessions: Any) -> None:
        actor_id = getattr(identity, "actor_id", None)
        if not isinstance(actor_id, str) or not actor_id.strip() or len(actor_id) > 128:
            raise ValueError("identity must contain a valid authenticated actor_id.")
        if not callable(getattr(sessions, "current_identity", None)):
            raise TypeError("sessions must provide current_identity().")
        if sessions.current_identity() != identity:
            raise PermissionError("The desktop session is no longer authenticated.")
        self._identity = identity
        self._sessions = sessions
        self._conversation = DesktopConversationAdapter(
            application, owner_id=actor_id,
        )

    @property
    def conversation_id(self) -> str | None:
        self._require_live_session()
        return self._conversation.conversation_id

    def _require_live_session(self) -> str:
        if self._sessions.current_identity() != self._identity:
            raise PermissionError("The desktop session expired. Sign in again.")
        return self._identity.actor_id

    def start_new_conversation(self, *, title: str = "New conversation") -> dict[str, Any]:
        self._require_live_session()
        return self._conversation.start_new_conversation(title=title)

    def select_conversation(self, conversation_id: str) -> dict[str, Any]:
        self._require_live_session()
        return self._conversation.select_conversation(conversation_id)

    def list_conversations(self, *, limit: int = 50) -> list[dict[str, Any]]:
        self._require_live_session()
        return self._conversation.list_conversations(limit=limit)

    def history(self, *, limit: int = 100) -> list[dict[str, Any]]:
        self._require_live_session()
        return self._conversation.history(limit=limit)

    def send(
        self,
        message: str,
        *,
        request_id: str | None = None,
        cancellation_requested: Callable[[], bool] | None = None,
        on_text_delta: Callable[[str], None] | None = None,
    ) -> dict[str, Any]:
        self._require_live_session()

        def guarded_delta(delta: str) -> None:
            self._require_live_session()
            if on_text_delta is not None:
                on_text_delta(delta)

        def guarded_cancel() -> bool:
            # The agent loop polls cancellation between model/stream events.
            # Session revocation therefore stops cooperative work promptly;
            # the post-turn check below remains the final response-release gate.
            try:
                self._require_live_session()
            except Exception:
                return True
            return bool(cancellation_requested()) if cancellation_requested is not None else False

        result = self._conversation.send(
            message,
            request_id=request_id,
            cancellation_requested=guarded_cancel,
            on_text_delta=guarded_delta if on_text_delta is not None else None,
        )
        # Do not return a completed response to a desktop surface whose
        # authenticated session was revoked while inference was in progress.
        self._require_live_session()
        return result

    def clear_selection(self) -> None:
        self._require_live_session()
        self._conversation.clear_selection()
