"""Owner-scoped, private SQLite storage for review-gated learning proposals.

This is authoritative review state, not Memory Fabric. It stores only narrow
application/domain identifiers and review metadata; it does not store page
content, screenshots, prompts, credentials, or raw conversations.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import re
import sqlite3
import threading
import time
import uuid
from typing import Optional

from core.sqlite_file_security import prepare_private_sqlite_path


_APP_ID = re.compile(r"[a-z0-9][a-z0-9_.:-]{0,127}\Z")
_DOMAIN = re.compile(
    r"(?=.{1,253}\Z)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+\Z"
)


@dataclass(frozen=True)
class KnowledgeProposal:
    proposal_id: str
    kind: str
    value: str
    summary: str
    fingerprint: str
    status: str
    created_at: float
    reviewed_at: Optional[float] = None


class SQLiteObservationKnowledgeReview:
    """Durable review registry with explicit owner scope and bounded retention.

    SQLite file permissions are private, but SQLite contents are not encrypted.
    Deploy on encrypted storage and apply OS account isolation.
    """

    def __init__(
        self, database_path: str, *, owner_id: str,
        capacity: int = 512, retention_seconds: int = 30 * 24 * 60 * 60,
        clock=time.time,
    ):
        if not isinstance(owner_id, str) or not owner_id.strip() or len(owner_id) > 256:
            raise ValueError("owner_id must be non-empty bounded text")
        if type(capacity) is not int or not 1 <= capacity <= 10000:
            raise ValueError("capacity must be an integer in 1..10000")
        if type(retention_seconds) is not int or not 60 <= retention_seconds <= 365 * 24 * 60 * 60:
            raise ValueError("retention_seconds must be between 60 seconds and one year")
        if not callable(clock):
            raise TypeError("clock must be callable")
        self._owner = owner_id.strip()
        self._capacity = capacity
        self._retention = retention_seconds
        self._clock = clock
        self._lock = threading.RLock()
        self.database_path = prepare_private_sqlite_path(database_path)
        with self._connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS observation_knowledge_proposals (
                proposal_id TEXT PRIMARY KEY,
                owner_id TEXT NOT NULL,
                kind TEXT NOT NULL CHECK(kind IN ('application','browser_domain')),
                value TEXT NOT NULL,
                summary TEXT NOT NULL,
                fingerprint TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('pending','approved','rejected')),
                created_at REAL NOT NULL,
                reviewed_at REAL,
                UNIQUE(owner_id, fingerprint)
            )""")
            db.execute("""CREATE INDEX IF NOT EXISTS idx_observation_proposals_owner_status
                ON observation_knowledge_proposals(owner_id, status, created_at)""")
            db.execute("""CREATE INDEX IF NOT EXISTS idx_observation_proposals_expiry
                ON observation_knowledge_proposals(created_at)""")

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.database_path, timeout=5.0, isolation_level=None)
        try:
            db.execute("PRAGMA busy_timeout=5000")
            yield db
        finally:
            db.close()

    @staticmethod
    def _fingerprint(kind: str, value: str) -> str:
        return hashlib.sha256(f"{kind}\0{value}".encode("utf-8")).hexdigest()

    @classmethod
    def _normalize(cls, kind: str, value: str) -> tuple[str, str]:
        if not isinstance(value, str):
            raise ValueError("proposal value must be text")
        value = value.strip().lower()
        if kind == "application" and _APP_ID.fullmatch(value):
            return kind, f"Observed allowlisted application: {value}"
        if kind == "browser_domain" and _DOMAIN.fullmatch(value):
            return kind, f"Observed allowlisted browser domain: {value}"
        raise ValueError("unsupported or invalid observation proposal")

    @staticmethod
    def _proposal(row) -> KnowledgeProposal:
        return KnowledgeProposal(*row)

    def _purge_expired(self, db, now: float) -> None:
        db.execute(
            "DELETE FROM observation_knowledge_proposals WHERE owner_id=? AND created_at<=?",
            (self._owner, now - self._retention),
        )

    def propose(self, kind: str, value: str) -> KnowledgeProposal:
        kind, summary = self._normalize(kind, value)
        value = value.strip().lower()
        fingerprint = self._fingerprint(kind, value)
        now = float(self._clock())
        with self._lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                self._purge_expired(db, now)
                row = db.execute("""SELECT proposal_id,kind,value,summary,fingerprint,status,created_at,reviewed_at
                    FROM observation_knowledge_proposals WHERE owner_id=? AND fingerprint=?""",
                    (self._owner, fingerprint)).fetchone()
                if row is not None:
                    db.execute("COMMIT")
                    return self._proposal(row)
                count = db.execute(
                    "SELECT COUNT(*) FROM observation_knowledge_proposals WHERE owner_id=?",
                    (self._owner,),
                ).fetchone()[0]
                if count >= self._capacity:
                    raise BufferError("knowledge proposal review capacity is full")
                proposal_id = uuid.uuid4().hex
                db.execute("""INSERT INTO observation_knowledge_proposals
                    (proposal_id,owner_id,kind,value,summary,fingerprint,status,created_at,reviewed_at)
                    VALUES (?,?,?,?,?,?,'pending',?,NULL)""",
                    (proposal_id, self._owner, kind, value, summary, fingerprint, now))
                db.execute("COMMIT")
                return KnowledgeProposal(proposal_id, kind, value, summary, fingerprint, "pending", now, None)
            except Exception:
                db.execute("ROLLBACK")
                raise

    def list_pending(self, *, limit: int = 100) -> tuple[KnowledgeProposal, ...]:
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("limit must be an integer in 1..1000")
        now = float(self._clock())
        with self._lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._purge_expired(db, now)
            rows = db.execute("""SELECT proposal_id,kind,value,summary,fingerprint,status,created_at,reviewed_at
                FROM observation_knowledge_proposals
                WHERE owner_id=? AND status='pending' ORDER BY created_at,proposal_id LIMIT ?""",
                (self._owner, limit)).fetchall()
            db.execute("COMMIT")
        return tuple(self._proposal(row) for row in rows)

    def get(self, proposal_id: str) -> Optional[KnowledgeProposal]:
        if not isinstance(proposal_id, str) or not proposal_id:
            return None
        with self._lock, self._connect() as db:
            row = db.execute("""SELECT proposal_id,kind,value,summary,fingerprint,status,created_at,reviewed_at
                FROM observation_knowledge_proposals WHERE owner_id=? AND proposal_id=?""",
                (self._owner, proposal_id)).fetchone()
        return self._proposal(row) if row else None

    def _review(self, proposal_id: str, decision: str) -> KnowledgeProposal:
        if not isinstance(proposal_id, str) or not proposal_id:
            raise ValueError("proposal_id is required")
        now = float(self._clock())
        with self._lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                cursor = db.execute("""UPDATE observation_knowledge_proposals
                    SET status=?,reviewed_at=? WHERE owner_id=? AND proposal_id=? AND status='pending'""",
                    (decision, now, self._owner, proposal_id))
                if cursor.rowcount != 1:
                    exists = db.execute("""SELECT 1 FROM observation_knowledge_proposals
                        WHERE owner_id=? AND proposal_id=?""", (self._owner, proposal_id)).fetchone()
                    if exists:
                        raise RuntimeError("knowledge proposal has already been reviewed")
                    raise KeyError("knowledge proposal not found")
                row = db.execute("""SELECT proposal_id,kind,value,summary,fingerprint,status,created_at,reviewed_at
                    FROM observation_knowledge_proposals WHERE owner_id=? AND proposal_id=?""",
                    (self._owner, proposal_id)).fetchone()
                db.execute("COMMIT")
                return self._proposal(row)
            except Exception:
                db.execute("ROLLBACK")
                raise

    def approve(self, proposal_id: str) -> KnowledgeProposal:
        """Record approval only; does not install memory or change behavior."""
        return self._review(proposal_id, "approved")

    def reject(self, proposal_id: str) -> KnowledgeProposal:
        return self._review(proposal_id, "rejected")

    def delete(self, proposal_id: str) -> bool:
        if not isinstance(proposal_id, str) or not proposal_id:
            raise ValueError("proposal_id is required")
        with self._lock, self._connect() as db:
            cursor = db.execute(
                "DELETE FROM observation_knowledge_proposals WHERE owner_id=? AND proposal_id=?",
                (self._owner, proposal_id),
            )
            return cursor.rowcount == 1

    def clear(self) -> int:
        with self._lock, self._connect() as db:
            cursor = db.execute(
                "DELETE FROM observation_knowledge_proposals WHERE owner_id=?",
                (self._owner,),
            )
            return cursor.rowcount

__all__ = ["KnowledgeProposal", "SQLiteObservationKnowledgeReview"]
