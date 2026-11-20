"""Owner-scoped native memory command adapters.

The application supplies a resolver that returns the authenticated actor's
CompanionMemory instance. The adapter never opens a global/default memory file.
Writes still require session-scoped memory consent; dispatcher confirmation and
durable pre-dispatch audit remain mandatory.
"""
from __future__ import annotations

import json
import re
from typing import Callable, Mapping

from companion.memory_consent import CompanionMemory
from core.native_intents import IntentKind


class NativeMemoryCommandAdapter:
    """Expose explicit memory search and remember commands to native dispatch."""

    _CAPABILITIES = {
        IntentKind.MEMORY_SEARCH: "memory:read",
        IntentKind.MEMORY_REMEMBER: "memory:write",
    }

    def __init__(
        self,
        memory_for_actor: Callable[[str], CompanionMemory],
        *,
        authorize_capability: Callable[[str, str], bool],
        search_limit: int = 10,
    ) -> None:
        if not callable(memory_for_actor):
            raise TypeError("memory_for_actor must be callable.")
        if not callable(authorize_capability):
            raise TypeError("authorize_capability must be callable.")
        if type(search_limit) is not int or not 1 <= search_limit <= 20:
            raise ValueError("search_limit must be an integer from 1 to 20.")
        self._memory_for_actor = memory_for_actor
        self._authorize_capability = authorize_capability
        self._search_limit = search_limit

    @property
    def handlers(self) -> Mapping[IntentKind, Callable[[str, tuple[str, ...]], str]]:
        return {
            IntentKind.MEMORY_SEARCH: self._search,
            IntentKind.MEMORY_REMEMBER: self._remember,
        }

    def authorize_intent(self, actor_id: str, kind: IntentKind) -> bool:
        capability = self._CAPABILITIES.get(kind)
        if capability is None:
            return False
        try:
            return self._authorize_capability(actor_id, capability) is True
        except Exception:
            return False

    def _memory(self, actor_id: str) -> CompanionMemory:
        # Resolver must bind the authenticated actor to a profile-private store.
        memory = self._memory_for_actor(actor_id)
        if not isinstance(memory, CompanionMemory):
            raise TypeError("actor memory resolver must return CompanionMemory.")
        return memory

    def _search(self, actor_id: str, arguments: tuple[str, ...]) -> str:
        if len(arguments) != 1 or not arguments[0].strip():
            raise ValueError("memory search requires exactly one non-empty query.")
        query = arguments[0].strip()
        memory = self._memory(actor_id)
        entries = memory.list_memories(limit=200)
        terms = {
            token.casefold()
            for token in re.findall(r"[^\\W_]{2,}", query, flags=re.UNICODE)
        }
        ranked = []
        for index, entry in enumerate(entries):
            text = entry.get("text", "")
            if not isinstance(text, str):
                continue
            words = {
                token.casefold()
                for token in re.findall(r"[^\\W_]{2,}", text, flags=re.UNICODE)
            }
            overlap = len(terms.intersection(words))
            if overlap:
                ranked.append((overlap, index, entry))
        ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
        matches = [
            {
                "id": entry["id"],
                "category": entry.get("category", "note"),
                "text": entry["text"][:1000],
                "created_at": entry["created_at"],
            }
            for _, _, entry in ranked[: self._search_limit]
        ]
        return json.dumps({"matches": matches}, ensure_ascii=False, separators=(",", ":"))

    def _remember(self, actor_id: str, arguments: tuple[str, ...]) -> str:
        if len(arguments) != 1 or not arguments[0].strip():
            raise ValueError("memory remember requires exactly one non-empty fact.")
        memory = self._memory(actor_id)
        # CompanionMemory enforces explicit session-scoped write consent.
        saved = memory.remember(arguments[0].strip(), category="note")
        return json.dumps(
            {"saved": True, "id": saved["id"], "category": saved.get("category", "note")},
            ensure_ascii=False,
            separators=(",", ":"),
        )


__all__ = ["NativeMemoryCommandAdapter"]
