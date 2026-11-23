"""Durable, owner-isolated SQLite queue for human-reviewed learning candidates."""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Any


class LearningQueueStore:
    """Persist pending/reviewed learning proposals with atomic status transitions."""

    def __init__(self, path: str | Path):
        raw = Path(path).expanduser()
        raw.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = raw.resolve()
        self._initialize()

    def _connect(self):
        connection = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 10000")
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _initialize(self):
        with self._connect() as db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS learning_candidates (
                    candidate_id TEXT PRIMARY KEY,
                    owner_id TEXT NOT NULL,
                    source TEXT NOT NULL CHECK(source IN ('foreground_feedback','background_task')),
                    lesson TEXT NOT NULL,
                    evidence TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending'
                        CHECK(status IN ('pending','approved','dismissed')),
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
            # Some platforms/filesystems do not implement POSIX mode bits.
            pass

    def add(self, candidate: Any) -> None:
        values = (
            candidate.candidate_id, candidate.owner_id, candidate.source,
            candidate.lesson, candidate.evidence, candidate.created_at,
        )
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("""
                INSERT INTO learning_candidates
                    (candidate_id, owner_id, source, lesson, evidence, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """, values)
            db.commit()

    def pending(self, owner_id: str, limit: int) -> list[dict]:
        with self._connect() as db:
            rows = db.execute("""
                SELECT candidate_id, owner_id, source, lesson, evidence, created_at
                FROM learning_candidates
                WHERE owner_id = ? AND status = 'pending'
                ORDER BY created_at, candidate_id LIMIT ?
            """, (owner_id, limit)).fetchall()
        return [dict(row) for row in rows]

    def get_pending(self, owner_id: str, candidate_id: str) -> dict | None:
        with self._connect() as db:
            row = db.execute("""
                SELECT candidate_id, owner_id, source, lesson, evidence, created_at
                FROM learning_candidates
                WHERE owner_id = ? AND candidate_id = ? AND status = 'pending'
            """, (owner_id, candidate_id)).fetchone()
        return dict(row) if row else None

    def resolve(self, owner_id: str, candidate_id: str, *, approved: bool) -> bool:
        from datetime import datetime, timezone
        status = "approved" if approved else "dismissed"
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            cursor = db.execute("""
                UPDATE learning_candidates
                SET status = ?, reviewed_at = ?
                WHERE owner_id = ? AND candidate_id = ? AND status = 'pending'
            """, (status, datetime.now(timezone.utc).isoformat(), owner_id, candidate_id))
            changed = cursor.rowcount == 1
            db.commit()
        return changed
