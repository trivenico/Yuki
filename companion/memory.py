"""Opt-in local memory facade backed by transactional SQLite state."""
from __future__ import annotations

from pathlib import Path
from uuid import uuid4
from datetime import datetime, timezone

from companion.state_store import CompanionStateStore


class MemoryStore:
    MAX_ENTRIES = 2000
    MAX_TEXT = 4000

    def __init__(self, path: str | Path = "yuki_companion.sqlite3", *, owner_id: str = "local",
                 state_store: CompanionStateStore | None = None):
        self.store = state_store or CompanionStateStore(path, owner_id=owner_id)

    def add(self, text: str, *, category: str = "note") -> dict:
        if not isinstance(text, str) or not text.strip() or len(text) > self.MAX_TEXT:
            raise ValueError(f"Memory text must be 1-{self.MAX_TEXT} characters.")
        if category not in {"note", "preference", "lesson"}:
            raise ValueError("category must be note, preference, or lesson.")
        item = {"id": uuid4().hex, "created_at": datetime.now(timezone.utc).isoformat(),
                "category": category, "text": text.strip()}
        return self.store.add_record("memory", item)

    def list(self, limit: int = 50) -> list[dict]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 200:
            raise ValueError("limit must be between 1 and 200.")
        return self.store.list_records("memory", limit=limit)

    def delete(self, memory_id: str) -> bool:
        return self.store.delete_record("memory", memory_id)

    def clear(self) -> int:
        """Explicit destructive operation; callers must obtain user approval first."""
        return self.store.clear_records("memory")
