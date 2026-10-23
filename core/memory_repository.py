"""Unified local memory boundary for Yuki.

This facade deliberately stays local-only. It does not sync, upload, or infer
permission to export personal memory.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from companion.memory import MemoryStore
from core.vector_memory import VectorMemory


class MemoryRepository:
    """One stable interface for structured notes and bounded lexical search."""

    def __init__(self, path: str | Path = "yuki_unified_memory.json"):
        self.path = Path(path).expanduser()
        self.store = MemoryStore(self.path)
        self.search_index = VectorMemory(self.path)

    def remember(self, text: str, *, category: str = "note") -> dict[str, Any]:
        return self.store.add(text, category=category)

    def list(self, *, limit: int = 50) -> list[dict[str, Any]]:
        return self.store.list(limit=limit)

    def search(self, query: str, *, limit: int = 5) -> list[dict[str, Any]]:
        return self.search_index.search(query, limit=limit)

    def delete(self, memory_id: str) -> bool:
        return self.store.delete(memory_id)

    def clear(self) -> int:
        """Clear only when the caller has already obtained user confirmation."""
        return self.store.clear()
