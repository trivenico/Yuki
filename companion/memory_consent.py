"""Consent-gated companion memory facade.

The store is inert by default. Nothing is persisted from conversation automatically:
callers must explicitly enable memory and explicitly submit a fact to remember.
Disabling memory prevents future writes but does not silently erase existing data.
"""
from __future__ import annotations

import os
import re

from companion.memory import MemoryStore


class MemoryConsentError(RuntimeError):
    """Raised when a caller tries to write without explicit memory consent."""


class CompanionMemory:
    """Explicit user-controlled access to Yuki's local companion memory."""

    def __init__(self, store: MemoryStore | None = None) -> None:
        if store is not None and not isinstance(store, MemoryStore):
            raise TypeError("store must be a MemoryStore instance.")
        if store is not None:
            self._store = store
        elif os.environ.get("YUKI_MEMORY_ENCRYPTION", "").strip().lower() in {"1", "true", "yes", "on"}:
            from companion.secure_memory import EncryptedMemoryStore
            self._store = EncryptedMemoryStore(os.environ.get("YUKI_MEMORY_PATH", "yuki_memory.enc.json"))
        else:
            self._store = MemoryStore(os.environ.get("YUKI_MEMORY_PATH", "yuki_memory.json"))
        self._enabled = False
        self._context_enabled = False

    @property
    def enabled(self) -> bool:
        """Consent is session-scoped; it is never inferred from stored entries."""
        return self._enabled

    @property
    def context_enabled(self) -> bool:
        """Whether saved entries may be included in this session's model context."""
        return self._context_enabled

    def enable(self) -> None:
        """Enable explicit remember operations for this running application session."""
        self._enabled = True

    def disable(self) -> None:
        """Stop future writes and context use without deleting existing saved memories."""
        self._enabled = False
        self._context_enabled = False

    def enable_context(self) -> None:
        """Explicitly allow saved notes to be considered in this session's chat context."""
        self._context_enabled = True

    def disable_context(self) -> None:
        """Stop adding saved notes to chat context for this session."""
        self._context_enabled = False

    def context_entries(self, *, query: str = "", limit: int = 10) -> list[dict]:
        """Return bounded, relevance-ranked notes only after session-scoped consent."""
        if not self._context_enabled:
            return []
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 10:
            raise ValueError("context limit must be between 1 and 10.")
        if not isinstance(query, str) or len(query) > 4000:
            raise ValueError("memory retrieval query must be a string up to 4000 characters.")
        entries = self._store.list(limit=200)
        tokens = {token.casefold() for token in re.findall(r"[^\W_]{2,}", query, flags=re.UNICODE)}
        if not tokens:
            return entries[-limit:]
        ranked = []
        for index, entry in enumerate(entries):
            text_tokens = {token.casefold() for token in re.findall(r"[^\W_]{2,}", entry.get("text", ""), flags=re.UNICODE)}
            overlap = len(tokens.intersection(text_tokens))
            if overlap:
                ranked.append((overlap, index, entry))
        ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
        return [entry for _, _, entry in ranked[:limit]]

    def remember(self, text: str, *, category: str = "note") -> dict:
        if not self._enabled:
            raise MemoryConsentError(
                "Memory is off. Explicitly enable memory before saving anything."
            )
        return self._store.add(text, category=category)

    def list_memories(self, *, limit: int = 50) -> list[dict]:
        """List saved entries; reading does not require write consent."""
        return self._store.list(limit=limit)

    def forget(self, memory_id: str) -> bool:
        """Delete one saved entry at the user's explicit request."""
        return self._store.delete(memory_id)

    def clear(self) -> int:
        """Delete all saved entries; callers must obtain explicit user confirmation."""
        return self._store.clear()
