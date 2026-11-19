"""Bind the shared desktop conversation adapter to a live authenticated session.

Authentication remains owned by the desktop session manager. This wrapper
rechecks that session at every operation and never accepts an owner ID from UI
input. Runtime shutdown remains the responsibility of the application lifecycle.
"""
from __future__ import annotations

from threading import Condition, RLock
from typing import Any, Callable

from ui.desktop.conversation_adapter import DesktopConversationAdapter


class AuthenticatedDesktopConversation:
    """Session-checked facade for one authenticated desktop account."""

    def __init__(self, application: Any, *, identity: Any, sessions: Any, memory: Any = None) -> None:
        actor_id = getattr(identity, "actor_id", None)
        if not isinstance(actor_id, str) or not actor_id.strip() or len(actor_id) > 128:
            raise ValueError("identity must contain a valid authenticated actor_id.")
        if not callable(getattr(sessions, "current_identity", None)):
            raise TypeError("sessions must provide current_identity().")
        if sessions.current_identity() != identity:
            raise PermissionError("The desktop session is no longer authenticated.")
        self._identity = identity
        self._sessions = sessions
        self._memory = memory
        self._invalidated = False
        self._lifecycle = Condition(RLock())
        self._active_sends = 0
        self._conversation = DesktopConversationAdapter(
            application, owner_id=actor_id, memory=memory,
        )

    @property
    def conversation_id(self) -> str | None:
        self._require_live_session()
        return self._conversation.conversation_id

    def _require_live_session(self) -> str:
        if self._invalidated or self._sessions.current_identity() != self._identity:
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
        with self._lifecycle:
            self._require_live_session()
            self._active_sends += 1

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

        try:
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
        finally:
            with self._lifecycle:
                self._active_sends -= 1
                self._lifecycle.notify_all()

    def set_memory_context_enabled(self, enabled: bool) -> None:
        """Apply explicit, session-scoped consent to memory retrieval."""
        self._require_live_session()
        if type(enabled) is not bool:
            raise ValueError("enabled must be a boolean.")
        if self._memory is None:
            if enabled:
                raise RuntimeError("No encrypted desktop memory store is configured.")
            return
        operation = (
            getattr(self._memory, "enable_context", None)
            if enabled else getattr(self._memory, "disable_context", None)
        )
        if not callable(operation):
            raise RuntimeError("The configured memory facade does not support consent controls.")
        operation()

    def memory_context_enabled(self) -> bool:
        """Report consent only while the authenticated session remains live."""
        self._require_live_session()
        if self._memory is None:
            return False
        enabled = getattr(self._memory, "context_enabled", False)
        if type(enabled) is not bool:
            raise RuntimeError("The configured memory consent state is invalid.")
        return enabled

    def disable_memory_context(self) -> None:
        """Fail-safe local revocation, usable during logout after session revocation."""
        if self._memory is None:
            return
        operation = getattr(self._memory, "disable_context", None)
        if not callable(operation):
            raise RuntimeError("The configured memory facade cannot revoke context consent.")
        operation()

    def invalidate(self) -> None:
        """Discard local session state and revoke memory context during logout."""
        # Close the authorization gate first, then wait for active sends to
        # observe cancellation and finish before the owning session closes the
        # SQLite runtime. This prevents teardown racing an in-flight DB commit.
        with self._lifecycle:
            if self._invalidated:
                return
            self._invalidated = True
            while self._active_sends:
                self._lifecycle.wait()
        errors = []
        try:
            self.disable_memory_context()
        except Exception as exc:
            errors.append(exc)
        try:
            self._conversation.clear_selection()
        except Exception as exc:
            errors.append(exc)
        if errors:
            raise RuntimeError(
                "Desktop session invalidation could not complete every cleanup step."
            ) from errors[0]

    def clear_selection(self) -> None:
        self._require_live_session()
        self._conversation.clear_selection()
