"""Durable owner-scoped catalog for explicitly promoted observation knowledge.

This is authoritative curated knowledge, not the reconstructible Memory Fabric
cache. Only the authorized review service should call promote().
"""
from __future__ import annotations

import hashlib
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager

from core.sqlite_file_security import prepare_private_sqlite_path


class SQLiteCuratedKnowledge:
    """Small durable catalog with source provenance and idempotent promotion."""

    def __init__(self, database_path: str, *, owner_id: str,
                 capacity: int = 10000, clock=time.time):
        if not isinstance(owner_id, str) or not owner_id.strip() or len(owner_id) > 256:
            raise ValueError("owner_id must be non-empty bounded text")
        if type(capacity) is not int or not 1 <= capacity <= 100000:
            raise ValueError("capacity must be an integer in 1..100000")
        if not callable(clock):
            raise TypeError("clock must be callable")
        self._owner = owner_id.strip()
        self._capacity = capacity
        self._clock = clock
        self._lock = threading.RLock()
        self.database_path = prepare_private_sqlite_path(database_path)
        with self._connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS curated_knowledge (
                entry_id TEXT PRIMARY KEY,
                owner_id TEXT NOT NULL,
                source_proposal_id TEXT NOT NULL,
                kind TEXT NOT NULL CHECK(kind IN ('application','browser_domain')),
                value TEXT NOT NULL,
                summary TEXT NOT NULL,
                fingerprint TEXT NOT NULL,
                approved_by TEXT NOT NULL,
                promoted_by TEXT NOT NULL,
                created_at REAL NOT NULL,
                active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
                UNIQUE(owner_id, source_proposal_id),
                UNIQUE(owner_id, fingerprint)
            )""")
            db.execute("""CREATE TABLE IF NOT EXISTS curated_knowledge_events (
                event_id TEXT PRIMARY KEY,
                owner_id TEXT NOT NULL,
                entry_id TEXT NOT NULL,
                action TEXT NOT NULL CHECK(action IN ('promoted','deactivated')),
                actor_id TEXT NOT NULL,
                created_at REAL NOT NULL
            )""")
            db.execute("""CREATE INDEX IF NOT EXISTS idx_curated_knowledge_owner_active
                ON curated_knowledge(owner_id, active, created_at)""")

    @contextmanager
    @property
    def owner_id(self) -> str:
        return self._owner

    def _connect(self):
        db = sqlite3.connect(self.database_path, timeout=5.0, isolation_level=None)
        try:
            db.execute("PRAGMA busy_timeout=5000")
            yield db
        finally:
            db.close()

    def promote(self, proposal, *, approved_by: str, promoted_by: str):
        if proposal.status != "approved":
            raise PermissionError("only approved proposals can be promoted")
        if proposal.kind not in {"application", "browser_domain"}:
            raise ValueError("unsupported knowledge kind")
        for name, value in (("approved_by", approved_by), ("promoted_by", promoted_by)):
            if not isinstance(value, str) or not value.strip() or len(value) > 200:
                raise ValueError(f"{name} must be non-empty bounded text")
        owner_fingerprint = hashlib.sha256(
            f"{proposal.kind}\\0{proposal.value}".encode("utf-8")
        ).hexdigest()
        now = float(self._clock())
        with self._lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                existing = db.execute("""SELECT entry_id FROM curated_knowledge
                    WHERE owner_id=? AND (source_proposal_id=? OR fingerprint=?)""",
                    (self._owner, proposal.proposal_id, owner_fingerprint)).fetchone()
                if existing:
                    db.execute("COMMIT")
                    return existing[0]
                count = db.execute(
                    "SELECT COUNT(*) FROM curated_knowledge WHERE owner_id=? AND active=1",
                    (self._owner,),
                ).fetchone()[0]
                if count >= self._capacity:
                    raise BufferError("curated knowledge capacity is full")
                entry_id = uuid.uuid4().hex
                db.execute("""INSERT INTO curated_knowledge
                    (entry_id,owner_id,source_proposal_id,kind,value,summary,fingerprint,
                     approved_by,promoted_by,created_at,active)
                    VALUES (?,?,?,?,?,?,?,?,?,?,1)""",
                    (entry_id,self._owner,proposal.proposal_id,proposal.kind,proposal.value,
                     proposal.summary,owner_fingerprint,approved_by.strip(),promoted_by.strip(),now))
                db.execute("""INSERT INTO curated_knowledge_events
                    (event_id,owner_id,entry_id,action,actor_id,created_at)
                    VALUES (?,?,?,'promoted',?,?)""",
                    (uuid.uuid4().hex,self._owner,entry_id,promoted_by.strip(),now))
                db.execute("COMMIT")
                return entry_id
            except Exception:
                if db.in_transaction:
                    db.execute("ROLLBACK")
                raise

    def list_active(self, *, limit: int = 100):
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("limit must be an integer in 1..1000")
        with self._lock, self._connect() as db:
            rows = db.execute("""SELECT entry_id,source_proposal_id,kind,value,summary,
                approved_by,promoted_by,created_at FROM curated_knowledge
                WHERE owner_id=? AND active=1 ORDER BY created_at DESC,entry_id LIMIT ?""",
                (self._owner,limit)).fetchall()
        fields=("entry_id","source_proposal_id","kind","value","summary",
                "approved_by","promoted_by","created_at")
        return tuple(dict(zip(fields,row)) for row in rows)

    def deactivate(self, entry_id: str, *, actor_id: str) -> bool:
        if not isinstance(entry_id,str) or not entry_id:
            raise ValueError("entry_id is required")
        if not isinstance(actor_id,str) or not actor_id.strip() or len(actor_id)>200:
            raise ValueError("actor_id must be non-empty bounded text")
        now=float(self._clock())
        with self._lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                cursor=db.execute("""UPDATE curated_knowledge SET active=0
                    WHERE owner_id=? AND entry_id=? AND active=1""",(self._owner,entry_id))
                if cursor.rowcount:
                    db.execute("""INSERT INTO curated_knowledge_events
                        (event_id,owner_id,entry_id,action,actor_id,created_at)
                        VALUES (?,?,?,'deactivated',?,?)""",
                        (uuid.uuid4().hex,self._owner,entry_id,actor_id.strip(),now))
                db.execute("COMMIT")
                return cursor.rowcount == 1
            except Exception:
                if db.in_transaction:
                    db.execute("ROLLBACK")
                raise


__all__ = ["SQLiteCuratedKnowledge"]
