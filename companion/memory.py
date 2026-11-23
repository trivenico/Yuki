"""Small local memory store with bounded entries and explicit deletion.

Memory is opt-in at the call site. No network, embeddings, or model training occur.
"""
from __future__ import annotations

import json
import math
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
            now = datetime.now(timezone.utc).isoformat()
            item = {"id": uuid4().hex, "created_at": now, "category": category,
                    "text": text.strip(), "confidence": 0.5, "status": "active",
                    "version": 1, "use_count": 0, "last_used_at": None,
                    "lifecycle_history": []}
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


    @staticmethod
    def _query_tokens(query: str) -> set[str]:
        import re
        return set(re.findall(r"[\\w]{2,}", query.casefold())[:64])

    def retrieve(self, query: str, *, limit: int = 5) -> list[dict]:
        """Return active lexical matches and atomically record their use.

        This is a deterministic local baseline, not semantic or vector search.
        Archived entries are never returned. Confidence affects ranking but
        does not override lexical relevance.
        """
        if not isinstance(query, str) or not query.strip() or len(query) > 4000:
            raise ValueError("query must be non-empty text up to 4000 characters.")
        if type(limit) is not int or not 1 <= limit <= 20:
            raise ValueError("limit must be between 1 and 20.")
        query_tokens = self._query_tokens(query)
        if not query_tokens:
            return []
        with self._lock, file_lock(self.path):
            entries = self._load()
            ranked = []
            for item in entries:
                if item.get("status", "active") != "active":
                    continue
                text = item.get("text")
                if not isinstance(text, str):
                    raise RuntimeError("Memory entry text is invalid.")
                tokens = self._query_tokens(text)
                overlap = len(query_tokens & tokens)
                if not overlap:
                    continue
                confidence = item.get("confidence", 0.5)
                if (
                    isinstance(confidence, bool) or not isinstance(confidence, (int, float))
                    or not math.isfinite(confidence) or not 0.0 <= confidence <= 1.0
                ):
                    raise RuntimeError("Memory confidence is invalid.")
                relevance = overlap / len(query_tokens)
                use_count = item.get("use_count", 0)
                if type(use_count) is not int or use_count < 0:
                    raise RuntimeError("Memory use counter is invalid.")
                score = relevance * (0.5 + 0.5 * confidence)
                ranked.append((score, item.get("created_at", ""), item))
            ranked.sort(key=lambda row: (row[0], row[1]), reverse=True)
            selected = [row[2] for row in ranked[:limit]]
            if selected:
                now = datetime.now(timezone.utc).isoformat()
                selected_ids = {item["id"] for item in selected}
                for item in entries:
                    if item["id"] in selected_ids:
                        item["use_count"] = item.get("use_count", 0) + 1
                        item["last_used_at"] = now
                self._save(entries)
            return [dict(item) for item in selected]

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


    def update_lifecycle(
        self, memory_id: str, *, expected_version: int,
        confidence: float | None = None, status: str | None = None,
        reason: str,
    ) -> dict | None:
        """Apply an explicit optimistic-concurrency-fenced lifecycle update."""
        if not isinstance(memory_id, str) or not memory_id.strip():
            raise ValueError("memory_id must be non-empty text.")
        if type(expected_version) is not int or expected_version < 1:
            raise ValueError("expected_version must be a positive integer.")
        if confidence is None and status is None:
            raise ValueError("at least one lifecycle field must change.")
        if confidence is not None and (
            isinstance(confidence, bool) or not isinstance(confidence, (int, float))
            or not math.isfinite(confidence) or not 0.0 <= confidence <= 1.0
        ):
            raise ValueError("confidence must be a finite number between 0 and 1.")
        if status is not None and status not in {"active", "archived"}:
            raise ValueError("status must be active or archived.")
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 500:
            raise ValueError("reason must be non-empty text up to 500 characters.")
        with self._lock, file_lock(self.path):
            entries = self._load()
            for item in entries:
                if item["id"] != memory_id:
                    continue
                version = item.get("version", 1)
                if type(version) is not int or version != expected_version:
                    raise RuntimeError("Memory version conflict.")
                history = item.setdefault("lifecycle_history", [])
                if not isinstance(history, list) or len(history) >= 100:
                    raise RuntimeError("Memory lifecycle history is invalid or full.")
                previous = {"version": version, "confidence": item.get("confidence", 0.5), "status": item.get("status", "active")}
                if confidence is not None:
                    item["confidence"] = float(confidence)
                if status is not None:
                    item["status"] = status
                item["version"] = version + 1
                history.append({
                    "action": "update", "from": previous,
                    "to": {"version": version + 1, "confidence": item["confidence"], "status": item["status"]},
                    "reason": reason.strip(), "changed_at": datetime.now(timezone.utc).isoformat(),
                })
                self._save(entries)
                return dict(item)
        return None

    def rollback_lifecycle(self, memory_id: str, *, expected_version: int, reason: str) -> dict | None:
        """Restore the previous lifecycle state while retaining append-only history."""
        if not isinstance(memory_id, str) or not memory_id.strip():
            raise ValueError("memory_id must be non-empty text.")
        if type(expected_version) is not int or expected_version < 2:
            raise ValueError("expected_version must be at least 2.")
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 500:
            raise ValueError("reason must be non-empty text up to 500 characters.")
        with self._lock, file_lock(self.path):
            entries = self._load()
            for item in entries:
                if item["id"] != memory_id:
                    continue
                if item.get("version", 1) != expected_version:
                    raise RuntimeError("Memory version conflict.")
                history = item.get("lifecycle_history", [])
                if not isinstance(history, list) or not history or len(history) >= 100:
                    raise RuntimeError("Lifecycle history is unavailable or full.")
                previous = history[-1].get("from")
                current = history[-1].get("to")
                if not isinstance(previous, dict) or not isinstance(current, dict):
                    raise RuntimeError("Lifecycle history is inconsistent.")
                item["confidence"] = previous["confidence"]
                item["status"] = previous["status"]
                item["version"] = expected_version + 1
                history.append({
                    "action": "rollback", "restored_from_version": previous["version"],
                    "from": current,
                    "to": {"version": item["version"], "confidence": item["confidence"], "status": item["status"]},
                    "reason": reason.strip(), "changed_at": datetime.now(timezone.utc).isoformat(),
                })
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
