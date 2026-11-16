"""Qt-independent bridge for the durable authenticated desktop chat session."""
from __future__ import annotations

from threading import Event, RLock
from typing import Any, Callable


class SharedDesktopChatController:
    """Small presentation controller backed only by the shared conversation bundle.

    The Qt launcher owns worker threads and marshals emitted deltas to the GUI
    thread. This class deliberately contains no widgets or provider lifecycle.
    """

    def __init__(self, conversation_session: Any) -> None:
        if not callable(getattr(conversation_session, "close", None)):
            raise TypeError("conversation_session must provide close().")
        conversation = getattr(conversation_session, "conversation", None)
        if not callable(getattr(conversation, "send", None)):
            raise TypeError("conversation_session must expose an authenticated conversation.")
        self._session = conversation_session
        self._conversation = conversation
        self._closed = False
        self._lock = RLock()
        self._active_cancellations: set[Event] = set()

    def respond(
        self,
        message: str,
        *,
        request_id: str | None = None,
        cancellation_requested: Callable[[], bool] | None = None,
        on_text_delta: Callable[[str], Any] | None = None,
    ) -> str:
        """Return the completed assistant answer for the existing desktop worker."""
        cancellation = Event()
        with self._lock:
            self._ensure_open()
            self._active_cancellations.add(cancellation)

        def should_cancel() -> bool:
            if cancellation.is_set():
                return True
            if cancellation_requested is None:
                return False
            try:
                return bool(cancellation_requested())
            except Exception:
                # A broken cancellation callback must never disable logout
                # cancellation or keep a task running without supervision.
                return True

        try:
            result = self._conversation.send(
                message,
                request_id=request_id,
                cancellation_requested=should_cancel,
                on_text_delta=on_text_delta,
            )
            self._ensure_open()
        finally:
            with self._lock:
                self._active_cancellations.discard(cancellation)
        if not isinstance(result, dict) or result.get("status") != "completed":
            status = result.get("status", "unknown") if isinstance(result, dict) else "invalid"
            raise RuntimeError(f"Yuki conversation did not complete (status: {status}).")
        answer = result.get("answer")
        if not isinstance(answer, str):
            raise RuntimeError("Yuki returned an invalid conversation response.")
        return answer

    def set_memory_context(self, enabled: bool) -> None:
        self._ensure_open()
        self._conversation.set_memory_context_enabled(enabled)

    def memory_context_enabled(self) -> bool:
        self._ensure_open()
        return self._conversation.memory_context_enabled()

    def logout(self) -> None:
        """Invalidate the authenticated facade and close its runtime exactly once."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            for cancellation in tuple(self._active_cancellations):
                cancellation.set()
        self._session.close()

    close = logout

    def _ensure_open(self) -> None:
        # Call while holding _lock when racing with logout.
        if self._closed:
            raise RuntimeError("desktop chat controller is closed.")
