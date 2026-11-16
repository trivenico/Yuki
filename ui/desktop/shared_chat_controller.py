"""Qt-independent bridge for the durable authenticated desktop chat session."""
from __future__ import annotations

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

    def respond(
        self,
        message: str,
        *,
        request_id: str | None = None,
        cancellation_requested: Callable[[], bool] | None = None,
        on_text_delta: Callable[[str], Any] | None = None,
    ) -> str:
        """Return the completed assistant answer for the existing desktop worker."""
        self._ensure_open()
        result = self._conversation.send(
            message,
            request_id=request_id,
            cancellation_requested=cancellation_requested,
            on_text_delta=on_text_delta,
        )
        self._ensure_open()
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
        if self._closed:
            return
        self._closed = True
        self._session.close()

    close = logout

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("desktop chat controller is closed.")
