"""Desktop-facing adapter over Yuki's canonical conversation runtime.

This module contains no Qt imports and owns no model, provider, or persistence
implementation. The authenticated desktop surface supplies its verified actor
ID; the shared runtime remains responsible for durable conversation semantics.
"""
from __future__ import annotations

import threading
import uuid
from typing import Any

from core.conversation_application_runtime import ConversationApplicationRuntime


class DesktopConversationAdapter:
    """Expose one owner's durable conversation to a single desktop session.

    Tool execution is deliberately disabled in this first adapter. Desktop
    agent plans continue through their separately authorized workflow surface;
    chat cannot silently acquire tool permissions as part of this integration.
    """

    def __init__(
        self,
        application: ConversationApplicationRuntime,
        *,
        owner_id: str,
    ) -> None:
        if not isinstance(application, ConversationApplicationRuntime):
            raise TypeError("application must be a ConversationApplicationRuntime.")
        if (
            not isinstance(owner_id, str)
            or not owner_id.strip()
            or len(owner_id.strip()) > 128
            or any(ord(char) < 32 or ord(char) == 127 for char in owner_id)
        ):
            raise ValueError("owner_id must be non-empty text of at most 128 characters.")
        if application.closed:
            raise RuntimeError("conversation runtime is closed.")
        self._application = application
        self._owner_id = owner_id.strip()
        self._conversation_id: str | None = None
        self._lock = threading.RLock()
        self._send_lock = threading.Lock()

    @property
    def conversation_id(self) -> str | None:
        """The currently selected conversation ID, if one has been opened."""
        with self._lock:
            return self._conversation_id

    def start_new_conversation(self, *, title: str = "New conversation") -> dict[str, Any]:
        """Create and select a fresh durable conversation for this owner."""
        with self._lock:
            self._ensure_open()
            conversation = self._application.conversations.create(
                owner_id=self._owner_id, title=title,
            )
            self._conversation_id = conversation["conversation_id"]
            return {
                "conversation_id": conversation["conversation_id"],
                "title": conversation["title"],
                "version": conversation["version"],
            }

    def select_conversation(self, conversation_id: str) -> dict[str, Any]:
        """Select an existing conversation only if it belongs to this owner."""
        with self._lock:
            self._ensure_open()
            conversation = self._application.store.get_conversation(
                owner_id=self._owner_id, conversation_id=conversation_id,
            )
            self._conversation_id = conversation["conversation_id"]
            return {
                "conversation_id": conversation["conversation_id"],
                "title": conversation["title"],
                "version": conversation["version"],
            }

    def list_conversations(self, *, limit: int = 50) -> list[dict[str, Any]]:
        """List only this authenticated owner's conversations."""
        with self._lock:
            self._ensure_open()
            return self._application.conversations.list(
                owner_id=self._owner_id, limit=limit,
            )

    def history(self, *, limit: int = 100) -> list[dict[str, Any]]:
        """Return persisted messages for the selected owner-scoped conversation."""
        with self._lock:
            self._ensure_open()
            conversation_id = self._require_conversation()
            return self._application.conversations.history(
                owner_id=self._owner_id,
                conversation_id=conversation_id,
                limit=limit,
            )

    def send(
        self,
        message: str,
        *,
        request_id: str | None = None,
        cancellation_requested=None,
        on_text_delta=None,
    ) -> dict[str, Any]:
        """Persist and execute one serialized turn through the canonical loop.

        The send lock serializes turns, while the state lock is held only long
        enough to snapshot the selected conversation and its version. Model
        inference therefore never blocks selection changes or UI cancellation.
        No tools are exposed by this desktop chat adapter.
        """
        if not isinstance(message, str) or not message.strip() or len(message) > 20_000:
            raise ValueError("message must be non-empty text of at most 20000 characters.")
        if request_id is None:
            request_id = str(uuid.uuid4())
        if (
            not isinstance(request_id, str)
            or not request_id.strip()
            or len(request_id) > 160
            or any(ord(char) < 32 or ord(char) == 127 for char in request_id)
        ):
            raise ValueError("request_id must be non-empty text of at most 160 characters.")
        if cancellation_requested is not None and not callable(cancellation_requested):
            raise TypeError("cancellation_requested must be callable.")
        if on_text_delta is not None and not callable(on_text_delta):
            raise TypeError("on_text_delta must be callable.")

        with self._send_lock:
            with self._lock:
                self._ensure_open()
                if self._conversation_id is None:
                    self.start_new_conversation()
                conversation_id = self._require_conversation()
                current = self._application.store.get_conversation(
                    owner_id=self._owner_id, conversation_id=conversation_id,
                )
                expected_version = current["version"]

            # Keep the selected conversation and version stable for this turn,
            # but do not hold the UI state lock during potentially slow inference.
            return self._application.conversations.reply(
                owner_id=self._owner_id,
                conversation_id=conversation_id,
                user_text=message.strip(),
                request_id=request_id.strip(),
                expected_version=expected_version,
                tools=[],
                cancellation_requested=cancellation_requested,
                on_text_delta=on_text_delta,
            )

    def clear_selection(self) -> None:
        """Forget the active UI selection without deleting durable history."""
        with self._lock:
            self._conversation_id = None

    def _require_conversation(self) -> str:
        if self._conversation_id is None:
            raise RuntimeError("no desktop conversation is selected.")
        return self._conversation_id

    def _ensure_open(self) -> None:
        if self._application.closed:
            raise RuntimeError("conversation runtime is closed.")
