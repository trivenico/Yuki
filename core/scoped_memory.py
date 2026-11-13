"""Per-actor local memory construction.

This is a storage isolation primitive, not an authentication mechanism. Callers
MUST pass an actor ID obtained from a verified server-side identity object.
Never pass a request-body, query-string, or other client-asserted actor ID.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

from companion.memory import MemoryStore
from core.vector_memory import VectorMemory


_ACTOR_ID = re.compile(r"^[^\x00-\x1f\x7f]{1,512}$")


def actor_storage_key(actor_id: str) -> str:
    """Return a stable, non-reversible filesystem key for a verified actor ID."""
    if not isinstance(actor_id, str) or not _ACTOR_ID.fullmatch(actor_id.strip()):
        raise ValueError("actor_id must be a non-empty verified identity value.")
    return hashlib.sha256(actor_id.strip().encode("utf-8")).hexdigest()


class ScopedMemory:
    """A memory facade bound to exactly one verified actor namespace."""

    def __init__(self, actor_id: str, *, root: str | Path = "data/user_memory"):
        key = actor_storage_key(actor_id)
        root_path = Path(root).expanduser().resolve()
        namespace = (root_path / key).resolve()
        if namespace.parent != root_path:
            raise ValueError("memory namespace escaped its configured root.")
        namespace.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            namespace.chmod(0o700)
        except OSError:
            # ACLs and non-POSIX filesystems may not expose chmod semantics.
            pass
        self.actor_key = key
        self._store = MemoryStore(namespace / "memories.json")
        self._retrieval = VectorMemory(namespace / "memories.json")
        # Share one lock and store instance across reads and writes to this namespace.
        self._retrieval.store = self._store

    def remember(self, text: str, *, category: str = "note") -> dict[str, Any]:
        return self._store.add(text, category=category)

    def update(
        self, memory_id: str, text: str, *, category: str | None = None
    ) -> dict[str, Any] | None:
        return self._store.update(memory_id, text, category=category)

    def list(self, *, limit: int = 50) -> list[dict[str, Any]]:
        return self._store.list(limit=limit)

    def search(
        self,
        query: str,
        *,
        limit: int = 5,
        category: str | None = None,
    ) -> list[dict[str, Any]]:
        return self._retrieval.search(query, limit=limit, category=category)

    def forget(self, memory_id: str) -> bool:
        return self._store.delete(memory_id)
