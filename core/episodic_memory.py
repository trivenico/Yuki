"""Consent-aware episodic memory for reviewed task summaries.

Only concise, explicitly supplied summaries are stored. Raw conversations,
prompts, model outputs and tool payloads are never captured automatically.
Episodes are untrusted reference data and cannot grant capabilities.
"""
from __future__ import annotations

import hashlib
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Any
from uuid import uuid4

_SECRET_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"(?i)\b(api[_ -]?key|password|secret|access[_ -]?token)\s*[:=]\s*\S+"),
    re.compile(r"(?i)\bbearer\s+\S+"),
)
_WORD = re.compile(r"[a-z0-9][a-z0-9_-]{1,40}", re.IGNORECASE)


def _owner_key(user: Any) -> str:
    user_id = getattr(user, "user_id", None)
    if not isinstance(user_id, str) or not user_id.strip():
        raise PermissionError("verified authenticated user required")
    return hashlib.sha256(user_id.strip().encode("utf-8")).hexdigest()


def _clean(value: str, field: str, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError(f"{field} must be 1-{maximum} characters")
    result = value.strip()
    for pattern in _SECRET_PATTERNS:
        result = pattern.sub("[REDACTED]", result)
    return result


class EpisodicMemoryService:
    """Per-owner task episode journal with consent, retention and explicit deletion."""

    MAX_EPISODES_PER_OWNER = 500

    def __init__(self, database_path: str | Path, memory_service: Any):
        if not callable(getattr(memory_service, "status", None)):
            raise TypeError("memory_service must provide status()")
        path = Path(database_path).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        self.database_path = str(path)
        self.memory_service = memory_service
        self._lock = RLock()
        with self._connect() as db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS episodic_memory (
                    episode_id TEXT PRIMARY KEY,
                    owner_key TEXT NOT NULL,
                    task_ref TEXT NOT NULL,
                    task_type TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    outcome TEXT NOT NULL,
                    tags_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(owner_key, task_ref)
                )
            """)
            db.execute("""
                CREATE INDEX IF NOT EXISTS idx_episode_owner_created
                ON episodic_memory(owner_key, created_at DESC)
            """)

    def _connect(self):
        db = sqlite3.connect(self.database_path, timeout=5.0)
        db.execute("PRAGMA busy_timeout=5000")
        return db

    def _require_consent(self, user: Any) -> str:
        owner = _owner_key(user)
        if not self.memory_service.status(user).get("enabled"):
            raise PermissionError("memory_consent_required")
        return owner

    @staticmethod
    def _to_dict(row):
        import json
        return {
            "id": row[0], "task_ref": row[1], "task_type": row[2],
            "summary": row[3], "outcome": row[4],
            "tags": json.loads(row[5]), "created_at": row[6],
            "category": "episode", "text": f"Task: {row[2]}. {row[3]} Outcome: {row[4]}",
        }

    def record(self, user: Any, *, task_ref: str, task_type: str,
               summary: str, outcome: str, tags: tuple[str, ...] | list[str] = ()) -> dict:
        """Record a distilled episode; callers must not pass raw conversation data."""
        owner = self._require_consent(user)
        task_ref = _clean(task_ref, "task_ref", 160)
        task_type = _clean(task_type, "task_type", 80)
        summary = _clean(summary, "summary", 1200)
        outcome = _clean(outcome, "outcome", 500)
        if not isinstance(tags, (tuple, list)) or len(tags) > 12:
            raise ValueError("tags must contain at most 12 strings")
        clean_tags = []
        for tag in tags:
            clean = _clean(tag, "tag", 40).lower()
            if clean not in clean_tags:
                clean_tags.append(clean)
        now = datetime.now(timezone.utc).isoformat()
        import json
        with self._lock, self._connect() as db:
            existing = db.execute(
                "SELECT episode_id, task_ref, task_type, summary, outcome, tags_json, created_at "
                "FROM episodic_memory WHERE owner_key=? AND task_ref=?",
                (owner, task_ref),
            ).fetchone()
            if existing:
                proposed = (task_type, summary, outcome, json.dumps(clean_tags, ensure_ascii=False))
                if tuple(existing[2:6]) != proposed:
                    raise ValueError("task_ref already has a different episode")
                return self._to_dict(existing)
            episode_id = uuid4().hex
            db.execute("""
                INSERT INTO episodic_memory
                (episode_id, owner_key, task_ref, task_type, summary, outcome, tags_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (episode_id, owner, task_ref, task_type, summary, outcome,
                  json.dumps(clean_tags, ensure_ascii=False), now))
            db.execute("""
                DELETE FROM episodic_memory
                WHERE owner_key=? AND episode_id NOT IN (
                    SELECT episode_id FROM episodic_memory
                    WHERE owner_key=? ORDER BY created_at DESC, episode_id DESC LIMIT ?
                )
            """, (owner, owner, self.MAX_EPISODES_PER_OWNER))
            row = db.execute(
                "SELECT episode_id, task_ref, task_type, summary, outcome, tags_json, created_at "
                "FROM episodic_memory WHERE owner_key=? AND episode_id=?",
                (owner, episode_id),
            ).fetchone()
        return self._to_dict(row)

    def search(self, user: Any, query: str, *, limit: int = 5) -> list[dict]:
        owner = self._require_consent(user)
        if not isinstance(query, str) or not query.strip() or len(query) > 20000:
            raise ValueError("query must be non-empty text up to 20000 characters")
        if type(limit) is not int or not 1 <= limit <= 20:
            raise ValueError("limit must be between 1 and 20")
        with self._connect() as db:
            rows = db.execute("""
                SELECT episode_id, task_ref, task_type, summary, outcome, tags_json, created_at
                FROM episodic_memory WHERE owner_key=?
                ORDER BY created_at DESC, episode_id DESC LIMIT 500
            """, (owner,)).fetchall()
        query_terms = {word.casefold() for word in _WORD.findall(query)}
        ranked = []
        for row in rows:
            item = self._to_dict(row)
            haystack = " ".join((item["task_type"], item["summary"], item["outcome"], *item["tags"])).casefold()
            terms = set(_WORD.findall(haystack))
            score = len(query_terms & terms)
            if score:
                ranked.append((score, item["created_at"], item))
        ranked.sort(key=lambda value: (value[0], value[1]), reverse=True)
        return [item for _, _, item in ranked[:limit]]

    def list_recent(self, user: Any, *, limit: int = 20) -> list[dict]:
        owner = self._require_consent(user)
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        with self._connect() as db:
            rows = db.execute("""
                SELECT episode_id, task_ref, task_type, summary, outcome, tags_json, created_at
                FROM episodic_memory WHERE owner_key=?
                ORDER BY created_at DESC, episode_id DESC LIMIT ?
            """, (owner, limit)).fetchall()
        return [self._to_dict(row) for row in rows]

    def forget(self, user: Any, episode_id: str) -> bool:
        owner = _owner_key(user)
        if not isinstance(episode_id, str) or not episode_id.strip():
            return False
        with self._connect() as db:
            cursor = db.execute(
                "DELETE FROM episodic_memory WHERE owner_key=? AND episode_id=?",
                (owner, episode_id),
            )
            return cursor.rowcount == 1
