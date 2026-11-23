"""Small local memory store with bounded entries and explicit deletion.

Memory is opt-in at the call site. No network, embeddings, or model training occur.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from uuid import uuid4

from core.json_file_store import atomic_write_json, file_lock


class MemoryStore:
    MAX_ENTRIES = 2000
    MAX_TEXT = 4000

    def __init__(self, path: str | Path = "yuki_memory.json"):
        self.path = Path(path).expanduser().resolve()
        self._lock = RLock()

    def _load(self) -> list[dict]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return []
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError("Memory file is unreadable; no data was overwritten.") from exc
        if not isinstance(raw, list) or len(raw) > self.MAX_ENTRIES:
            raise RuntimeError("Memory file has an invalid structure or exceeds limits.")
        for item in raw:
            if not isinstance(item, dict) or not all(k in item for k in ("id", "created_at", "text")):
                raise RuntimeError("Memory file contains an invalid entry.")
        return raw

    def _save(self, entries: list[dict]) -> None:
        atomic_write_json(self.path, entries[-self.MAX_ENTRIES:])

    def add(self, text: str, *, category: str = "note", source_id: str | None = None) -> dict:
        if not isinstance(text, str) or not text.strip() or len(text) > self.MAX_TEXT:
            raise ValueError(f"Memory text must be 1-{self.MAX_TEXT} characters.")
        if category not in {"note", "preference", "lesson"}:
            raise ValueError("category must be note, preference, or lesson.")
        if source_id is not None and (not isinstance(source_id, str) or not source_id.strip() or len(source_id) > 128):
            raise ValueError("source_id must be non-empty text of at most 128 characters.")
        with self._lock, file_lock(self.path):
            entries = self._load()
            if source_id is not None:
                existing = next((entry for entry in entries if entry.get("source_id") == source_id), None)
                if existing is not None:
                    return dict(existing)
            item = {"id": uuid4().hex, "created_at": datetime.now(timezone.utc).isoformat(),
                    "category": category, "text": text.strip()}
            if source_id is not None:
                item["source_id"] = source_id
            entries.append(item)
            self._save(entries)
            return dict(item)

    def list(self, limit: int = 50) -> list[dict]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 200:
            raise ValueError("limit must be between 1 and 200.")
        with self._lock, file_lock(self.path):
            return [dict(item) for item in self._load()[-limit:]]


    def record_use(self, memory_id: str) -> dict | None:
        """Record retrieval usage without changing the saved memory content."""
        if not isinstance(memory_id, str) or not memory_id.strip():
            raise ValueError("memory_id must be non-empty text.")
        with self._lock, file_lock(self.path):
            entries = self._load()
            for item in entries:
                if item["id"] == memory_id:
                    count = item.get("use_count", 0)
                    if type(count) is not int or count < 0:
                        raise RuntimeError("Memory use counter is invalid.")
                    item["use_count"] = count + 1
                    item["last_used_at"] = datetime.now(timezone.utc).isoformat()
                    self._save(entries)
                    return dict(item)
        return None

    def delete(self, memory_id: str) -> bool:
        if not isinstance(memory_id, str) or not memory_id.strip():
            return False
        with self._lock, file_lock(self.path):
            entries = self._load()
            remaining = [item for item in entries if item["id"] != memory_id]
            if len(remaining) == len(entries):
                return False
            self._save(remaining)
            return True

    def clear(self) -> int:
        """Explicit destructive operation; callers must obtain user approval first."""
        with self._lock, file_lock(self.path):
            entries = self._load()
            self._save([])
            return len(entries)
