"""Authenticated-user memory service with explicit consent and scoped storage."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from core.memory_consent import MemoryConsentStore
from core.scoped_memory import ScopedMemory


class UserMemoryService:
    """Bind memory operations to an AuthenticatedUser supplied by the API layer."""

    def __init__(self, root: str | Path, consent_database: str | Path):
        self.root = Path(root).expanduser()
        self.consent = MemoryConsentStore(consent_database)

    @staticmethod
    def _user_id(user: Any) -> str:
        user_id = getattr(user, "user_id", None)
        if not isinstance(user_id, str) or not user_id:
            raise PermissionError("verified authenticated user required")
        return user_id

    def status(self, user: Any) -> dict:
        user_id = self._user_id(user)
        return {"enabled": self.consent.enabled(user_id)}

    def set_enabled(self, user: Any, enabled: bool) -> dict:
        user_id = self._user_id(user)
        state = self.consent.set_enabled(user_id, enabled)
        if not enabled:
            # Disabling stops future reads/writes. It does not silently delete
            # existing memories; explicit deletion/export remains available.
            return state
        return state

    def _memory(self, user: Any) -> ScopedMemory:
        return ScopedMemory(self._user_id(user), root=self.root)

    def remember(self, user: Any, text: str, *, category: str = "note") -> dict:
        user_id = self._user_id(user)
        if not self.consent.enabled(user_id):
            raise PermissionError("memory_consent_required")
        return self._memory(user).remember(text, category=category)

    def list(self, user: Any, *, limit: int = 50) -> list[dict]:
        user_id = self._user_id(user)
        if not self.consent.enabled(user_id):
            return []
        return self._memory(user).list(limit=limit)

    def search(self, user: Any, query: str, *, limit: int = 5,
               category: str | None = None) -> list[dict]:
        user_id = self._user_id(user)
        if not self.consent.enabled(user_id):
            return []
        return self._memory(user).search(query, limit=limit, category=category)

    def edit(self, user: Any, memory_id: str, text: str,
             *, category: str | None = None) -> dict:
        user_id = self._user_id(user)
        if not self.consent.enabled(user_id):
            raise PermissionError("memory_consent_required")
        memory = self._memory(user)
        updated = memory.update(memory_id, text, category=category)
        if updated is None:
            raise KeyError("memory_not_found")
        return updated

    def forget(self, user: Any, memory_id: str) -> bool:
        return self._memory(user).forget(memory_id)

    def export(self, user: Any) -> dict:
        user_id = self._user_id(user)
        return {"memory_enabled": self.consent.enabled(user_id),
                "memories": self.list(user)}
