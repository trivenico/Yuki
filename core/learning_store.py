"""Durable, owner-isolated SQLite queue for human-reviewed learning candidates."""
from __future__ import annotations

import os
import re
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


class LearningQueueStore:
    """Persist pending/reviewed learning proposals with atomic status transitions."""

    def __init__(self, path: str | Path):
        raw = Path(path).expanduser()
        if raw.is_symlink():
            raise ValueError("learning queue database must not be a symlink.")
        raw.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = raw.parent.resolve() / raw.name
        if self.path.exists() and (self.path.is_symlink() or not self.path.is_file()):
            raise ValueError("learning queue database must be a regular file.")
        self._initialize()

    def _connect(self):
        connection = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 10000")
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _initialize(self):
        with closing(self._connect()) as db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS learning_candidates (
                    candidate_id TEXT PRIMARY KEY,
                    owner_id TEXT NOT NULL,
                    source TEXT NOT NULL CHECK(source IN ('foreground_feedback','background_task')),
                    lesson TEXT NOT NULL,
                    evidence TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending'
                        CHECK(status IN ('pending','reviewing','approved','dismissed')),
                    reviewing_at TEXT,
                    reviewed_at TEXT
                )
            """)
            db.execute("""
                CREATE INDEX IF NOT EXISTS idx_learning_owner_pending
                ON learning_candidates(owner_id, status, created_at)
            """)
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    @staticmethod
    def _normalized_tokens(value: str) -> set[str]:
        return set(re.findall(r"[\\w]{2,}", value.casefold()))

    @classmethod
    def _is_duplicate_lesson(cls, left: str, right: str) -> bool:
        normalized_left = " ".join(left.casefold().split())
        normalized_right = " ".join(right.casefold().split())
        if normalized_left == normalized_right:
            return True
        left_tokens = cls._normalized_tokens(normalized_left)
        right_tokens = cls._normalized_tokens(normalized_right)
        if not left_tokens or not right_tokens:
            return False
        return len(left_tokens & right_tokens) / len(left_tokens | right_tokens) >= 0.9

    def add(self, candidate: Any) -> None:
        values = (
            candidate.candidate_id, candidate.owner_id, candidate.source,
            candidate.lesson, candidate.evidence, candidate.created_at,
        )
        with closing(self._connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            count = db.execute(
                "SELECT COUNT(*) FROM learning_candidates WHERE owner_id = ? AND status IN ('pending','reviewing')",
                (candidate.owner_id,),
            ).fetchone()[0]
            if count >= 200:
                db.rollback()
                raise RuntimeError("learning review queue is full.")
            existing_lessons = db.execute(
                "SELECT lesson FROM learning_candidates WHERE owner_id = ? AND status IN ('pending','reviewing')",
                (candidate.owner_id,),
            ).fetchall()
            if any(self._is_duplicate_lesson(candidate.lesson, row["lesson"]) for row in existing_lessons):
                db.rollback()
                raise ValueError("learning candidate duplicates a pending lesson.")
            db.execute("""
                INSERT INTO learning_candidates
                    (candidate_id, owner_id, source, lesson, evidence, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """, values)
            db.commit()

    def pending(self, owner_id: str, limit: int) -> list[dict]:
        with closing(self._connect()) as db:
            rows = db.execute("""
                SELECT candidate_id, owner_id, source, lesson, evidence, created_at
                FROM learning_candidates
                WHERE owner_id = ? AND status = 'pending'
                ORDER BY created_at, candidate_id LIMIT ?
            """, (owner_id, limit)).fetchall()
        return [dict(row) for row in rows]

    def get_reviewing(self, owner_id: str, candidate_id: str) -> dict | None:
        with closing(self._connect()) as db:
            row = db.execute("""
                SELECT candidate_id, owner_id, source, lesson, evidence, created_at
                FROM learning_candidates
                WHERE owner_id = ? AND candidate_id = ? AND status = 'reviewing'
            """, (owner_id, candidate_id)).fetchone()
        return dict(row) if row else None

    def claim(self, owner_id: str, candidate_id: str) -> bool:
        now = datetime.now(timezone.utc)
        stale_before = (now - timedelta(minutes=5)).isoformat()
        with closing(self._connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            cursor = db.execute("""
                UPDATE learning_candidates
                SET status = 'reviewing', reviewing_at = ?
                WHERE owner_id = ? AND candidate_id = ?
                  AND (status = 'pending' OR
                       (status = 'reviewing' AND reviewing_at < ?))
            """, (now.isoformat(), owner_id, candidate_id, stale_before))
            changed = cursor.rowcount == 1
            db.commit()
        return changed

    def resolve(self, owner_id: str, candidate_id: str, *, approved: bool) -> bool:
        status = "approved" if approved else "dismissed"
        with closing(self._connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            cursor = db.execute("""
                UPDATE learning_candidates
                SET status = ?, reviewed_at = ?, reviewing_at = NULL
                WHERE owner_id = ? AND candidate_id = ? AND status = 'reviewing'
            """, (status, datetime.now(timezone.utc).isoformat(), owner_id, candidate_id))
            changed = cursor.rowcount == 1
            db.commit()
        return changed

    def release(self, owner_id: str, candidate_id: str) -> bool:
        with closing(self._connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            cursor = db.execute("""
                UPDATE learning_candidates
                SET status = 'pending', reviewing_at = NULL
                WHERE owner_id = ? AND candidate_id = ? AND status = 'reviewing'
            """, (owner_id, candidate_id))
            changed = cursor.rowcount == 1
            db.commit()
        return changed
