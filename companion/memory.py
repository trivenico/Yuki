"""Small local memory store with bounded entries and explicit deletion.

Memory is opt-in at the call site. No network, embeddings, or model training occur.
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from uuid import uuid4


class MemoryStore:
    MAX_ENTRIES = 2000
    MAX_TEXT = 4000
    MAX_FILE_BYTES = 10_000_000

    def __init__(self, path: str | Path = "yuki_memory.json"):
        self.path = Path(path).expanduser().resolve()
        self._lock = RLock()

    def _load(self) -> list[dict]:
        try:
            if self.path.stat().st_size > self.MAX_FILE_BYTES:
                raise RuntimeError("Memory file exceeds the 10 MB safety limit.")
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
            if (
                not isinstance(item["id"], str) or not item["id"] or len(item["id"]) > 64
                or not isinstance(item["created_at"], str) or not item["created_at"] or len(item["created_at"]) > 80
                or not isinstance(item["text"], str) or not item["text"].strip()
                or len(item["text"]) > self.MAX_TEXT
                or not isinstance(item.get("category", "note"), str)
                or item.get("category", "note") not in {"note", "preference", "lesson"}
            ):
                raise RuntimeError("Memory file contains an invalid entry.")
        return raw

    def _save(self, entries: list[dict]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix=".yuki-memory-", suffix=".tmp", dir=str(self.path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(entries[-self.MAX_ENTRIES:], handle, ensure_ascii=False, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, self.path)
        finally:
            try:
                os.unlink(temp_name)
            except FileNotFoundError:
                pass

    def add(self, text: str, *, category: str = "note") -> dict:
        if not isinstance(text, str) or not text.strip() or len(text) > self.MAX_TEXT:
            raise ValueError(f"Memory text must be 1-{self.MAX_TEXT} characters.")
        if category not in {"note", "preference", "lesson"}:
            raise ValueError("category must be note, preference, or lesson.")
        with self._lock:
            entries = self._load()
            item = {"id": uuid4().hex, "created_at": datetime.now(timezone.utc).isoformat(),
                    "category": category, "text": text.strip()}
            entries.append(item)
            self._save(entries)
            return dict(item)

    def list(self, limit: int = 50) -> list[dict]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 200:
            raise ValueError("limit must be between 1 and 200.")
        with self._lock:
            return [dict(item) for item in self._load()[-limit:]]

    def delete(self, memory_id: str) -> bool:
        if not isinstance(memory_id, str) or not memory_id.strip():
            return False
        with self._lock:
            entries = self._load()
            remaining = [item for item in entries if item["id"] != memory_id]
            if len(remaining) == len(entries):
                return False
            self._save(remaining)
            return True

    def clear(self) -> int:
        """Explicit destructive operation; callers must obtain user approval first."""
        with self._lock:
            entries = self._load()
            self._save([])
            return len(entries)
