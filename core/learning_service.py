"""Consent-aware, human-approved task learning for Yuki.

This records reviewed lessons as personal memory. It does not train, fine-tune,
or modify model weights, prompts, policies, or tool permissions.
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


def _safe_text(value: str, *, field: str, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError(f"{field} must be 1-{maximum} characters.")
    cleaned = value.strip()
    for pattern in _SECRET_PATTERNS:
        cleaned = pattern.sub("[REDACTED]", cleaned)
    return cleaned


def _owner_key(user: Any) -> str:
    user_id = getattr(user, "user_id", None)
    if not isinstance(user_id, str) or not user_id.strip():
        raise PermissionError("verified authenticated user required")
    return hashlib.sha256(user_id.strip().encode("utf-8")).hexdigest()


class LearningService:
    """Persist proposals per verified user; only explicit approval writes memory."""

    def __init__(self, database_path: str | Path, memory_service: Any):
        if not callable(getattr(memory_service, "remember", None)):
            raise TypeError("memory_service must provide remember().")
        if not callable(getattr(memory_service, "status", None)):
            raise TypeError("memory_service must provide status().")
        self.memory_service = memory_service
        path = Path(database_path).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        self.database_path = str(path)
        self._lock = RLock()
        with self._connect() as db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS learning_proposals (
                    proposal_id TEXT PRIMARY KEY,
                    owner_key TEXT NOT NULL,
                    task_ref TEXT NOT NULL,
                    lesson TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('pending', 'approved', 'rejected')),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    memory_id TEXT,
                    UNIQUE(owner_key, task_ref)
                )
            """)
            db.execute("""
                CREATE INDEX IF NOT EXISTS idx_learning_owner_status
                ON learning_proposals(owner_key, status, created_at)
            """)

    def _connect(self):
        db = sqlite3.connect(self.database_path, timeout=5.0)
        db.execute("PRAGMA busy_timeout=5000")
        return db

    def propose(self, user: Any, *, task_ref: str, lesson: str) -> dict:
        owner = _owner_key(user)
        if not self.memory_service.status(user).get("enabled"):
            raise PermissionError("memory_consent_required")
        task_ref = _safe_text(task_ref, field="task_ref", maximum=160)
        lesson = _safe_text(lesson, field="lesson", maximum=2000)
        now = datetime.now(timezone.utc).isoformat()
        with self._lock, self._connect() as db:
            existing = db.execute(
                "SELECT proposal_id, status, lesson, created_at, updated_at, memory_id "
                "FROM learning_proposals WHERE owner_key=? AND task_ref=?",
                (owner, task_ref),
            ).fetchone()
            if existing:
                if existing[2] != lesson:
                    raise ValueError("task_ref already has a different learning proposal")
                return self._record(existing)
            proposal_id = uuid4().hex
            db.execute("""
                INSERT INTO learning_proposals
                (proposal_id, owner_key, task_ref, lesson, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, 'pending', ?, ?)
            """, (proposal_id, owner, task_ref, lesson, now, now))
            return {
                "proposal_id": proposal_id, "task_ref": task_ref,
                "lesson": lesson, "status": "pending",
                "created_at": now, "updated_at": now, "memory_id": None,
            }

    @staticmethod
    def _record(row):
        return {
            "proposal_id": row[0], "status": row[1], "lesson": row[2],
            "created_at": row[3], "updated_at": row[4], "memory_id": row[5],
        }

    def list_pending(self, user: Any, *, limit: int = 50) -> list[dict]:
        owner = _owner_key(user)
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100.")
        with self._connect() as db:
            rows = db.execute("""
                SELECT proposal_id, status, lesson, created_at, updated_at, memory_id
                FROM learning_proposals
                WHERE owner_key=? AND status='pending'
                ORDER BY created_at DESC LIMIT ?
            """, (owner, limit)).fetchall()
        return [self._record(row) for row in rows]

    def approve(self, user: Any, proposal_id: str) -> dict:
        owner = _owner_key(user)
        if not isinstance(proposal_id, str) or not proposal_id.strip():
            raise ValueError("proposal_id is required.")
        if not self.memory_service.status(user).get("enabled"):
            raise PermissionError("memory_consent_required")
        with self._lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("""
                SELECT proposal_id, status, lesson, created_at, updated_at, memory_id
                FROM learning_proposals WHERE owner_key=? AND proposal_id=?
            """, (owner, proposal_id)).fetchone()
            if row is None:
                raise KeyError("learning_proposal_not_found")
            if row[1] == "rejected":
                raise ValueError("rejected proposal cannot be approved")
            if row[1] == "approved":
                return self._record(row)
            # The only path that writes a lesson into personal memory.
            memory = self.memory_service.remember(user, row[2], category="lesson")
            now = datetime.now(timezone.utc).isoformat()
            db.execute("""
                UPDATE learning_proposals
                SET status='approved', updated_at=?, memory_id=?
                WHERE owner_key=? AND proposal_id=? AND status='pending'
            """, (now, memory["id"], owner, proposal_id))
            return {
                "proposal_id": proposal_id, "status": "approved",
                "lesson": row[2], "created_at": row[3],
                "updated_at": now, "memory_id": memory["id"],
            }

    def reject(self, user: Any, proposal_id: str) -> bool:
        owner = _owner_key(user)
        if not isinstance(proposal_id, str) or not proposal_id.strip():
            raise ValueError("proposal_id is required.")
        now = datetime.now(timezone.utc).isoformat()
        with self._lock, self._connect() as db:
            cursor = db.execute("""
                UPDATE learning_proposals SET status='rejected', updated_at=?
                WHERE owner_key=? AND proposal_id=? AND status='pending'
            """, (now, owner, proposal_id))
            if cursor.rowcount:
                return True
            row = db.execute(
                "SELECT status FROM learning_proposals WHERE owner_key=? AND proposal_id=?",
                (owner, proposal_id),
            ).fetchone()
            if row is None:
                raise KeyError("learning_proposal_not_found")
            return False
