"""Qt-independent bridge for the durable authenticated desktop chat session."""
from __future__ import annotations

from math import isfinite
from threading import Condition, Event, RLock
from time import monotonic
from typing import Any, Callable


class SharedDesktopChatController:
    """Small presentation controller backed only by the shared conversation bundle.

    The Qt launcher owns worker threads and marshals emitted deltas to the GUI
    thread. This class deliberately contains no widgets or provider lifecycle.
    """

    def __init__(self, conversation_session: Any, *, shutdown_timeout_seconds: float = 30.0) -> None:
        if not callable(getattr(conversation_session, "close", None)):
            raise TypeError("conversation_session must provide close().")
        conversation = getattr(conversation_session, "conversation", None)
        if not callable(getattr(conversation, "send", None)):
            raise TypeError("conversation_session must expose an authenticated conversation.")
        if isinstance(shutdown_timeout_seconds, bool) or not isinstance(shutdown_timeout_seconds, (int, float)):
            raise TypeError("shutdown_timeout_seconds must be a finite number")
        if not 0 <= shutdown_timeout_seconds <= 3600 or not isfinite(shutdown_timeout_seconds):
            raise ValueError("shutdown_timeout_seconds must be between 0 and 3600 seconds")
        self._shutdown_timeout_seconds = float(shutdown_timeout_seconds)
        self._session = conversation_session
        self._conversation = conversation
        self._closed = False
        self._lock = RLock()
        self._condition = Condition(self._lock)
        self._active_cancellations: set[Event] = set()
        self._active_operations = 0
        self._session_close_started = False
        self._session_close_complete = False

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
            self._active_operations += 1

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
            with self._condition:
                self._active_cancellations.discard(cancellation)
                self._active_operations -= 1
                self._condition.notify_all()
        if not isinstance(result, dict) or result.get("status") != "completed":
            status = result.get("status", "unknown") if isinstance(result, dict) else "invalid"
            raise RuntimeError(f"Yuki conversation did not complete (status: {status}).")
        answer = result.get("answer")
        if not isinstance(answer, str):
            raise RuntimeError("Yuki returned an invalid conversation response.")
        return answer

    def list_conversations(self, *, limit: int = 50) -> list[dict[str, Any]]:
        self._begin_operation()
        try:
            return self._conversation.list_conversations(limit=limit)
        finally:
            self._end_operation()

    def select_conversation(self, conversation_id: str) -> dict[str, Any]:
        self._begin_operation()
        try:
            return self._conversation.select_conversation(conversation_id)
        finally:
            self._end_operation()

    def history(self, *, limit: int = 100) -> list[dict[str, Any]]:
        self._begin_operation()
        try:
            return self._conversation.history(limit=limit)
        finally:
            self._end_operation()

    def set_memory_context(self, enabled: bool) -> None:
        self._begin_operation()
        try:
            self._conversation.set_memory_context_enabled(enabled)
        finally:
            self._end_operation()

    def memory_context_enabled(self) -> bool:
        self._begin_operation()
        try:
            return self._conversation.memory_context_enabled()
        finally:
            self._end_operation()

    def logout(self) -> None:
        """Invalidate the authenticated facade and close its runtime exactly once."""
        deadline = monotonic() + self._shutdown_timeout_seconds
        with self._condition:
            if self._session_close_complete:
                return
            if not self._closed:
                self._closed = True
                for cancellation in tuple(self._active_cancellations):
                    cancellation.set()
            # Never close the durable store underneath an operation that may
            # still be using it. Bound the wait; on timeout leave it open and
            # keep the authorization gate closed so a later call can retry.
            while self._active_operations:
                remaining = deadline - monotonic()
                if remaining <= 0 or not self._condition.wait(timeout=remaining):
                    raise TimeoutError("desktop chat operations did not drain before shutdown deadline")
            while self._session_close_started:
                remaining = deadline - monotonic()
                if remaining <= 0 or not self._condition.wait(timeout=remaining):
                    raise TimeoutError("another desktop chat close operation did not finish before deadline")
                if self._session_close_complete:
                    return
            self._session_close_started = True
        try:
            self._session.close()
        except Exception:
            with self._condition:
                self._session_close_started = False
                self._condition.notify_all()
            raise
        with self._condition:
            self._session_close_started = False
            self._session_close_complete = True
            self._condition.notify_all()

    close = logout

    def _begin_operation(self) -> None:
        with self._lock:
            self._ensure_open()
            self._active_operations += 1

    def _end_operation(self) -> None:
        with self._condition:
            self._active_operations -= 1
            self._condition.notify_all()

    def _ensure_open(self) -> None:
        # RLock makes this safe both inside and outside existing critical sections.
        # In particular, a response finishing concurrently with logout must not
        # read the lifecycle flag without synchronization.
        with self._lock:
            if self._closed:
                raise RuntimeError("desktop chat controller is closed.")
