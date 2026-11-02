"""Human-reviewed memory proposals.

Proposals are untrusted suggestions and are never included in memory retrieval
until explicitly approved by the authenticated owner.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import secrets
import sqlite3

from core.memory import Memory, MemoryStore, _text, _CATEGORIES


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class MemoryProposal:
    proposal_id: str
    actor_id: str
    scope_id: str
    category: str
    content: str
    status: str
    created_at: str
    updated_at: str
    memory_id: str | None


class MemoryReviewStore:
    """Persist proposals with owner/scope isolation and explicit approval."""

    def __init__(self, database_path: str | Path, memory_store: MemoryStore):
        self.path = str(Path(database_path).expanduser())
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.memory_store = memory_store
        with self._connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS yuki_memory_proposals (
                proposal_id TEXT PRIMARY KEY,
                actor_id TEXT NOT NULL,
                scope_id TEXT NOT NULL,
                category TEXT NOT NULL,
                content TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('pending','approved','rejected')),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                memory_id TEXT,
                UNIQUE(actor_id, scope_id, proposal_id)
            )""")
            db.execute("""CREATE INDEX IF NOT EXISTS idx_yuki_proposals_owner
                ON yuki_memory_proposals(actor_id, scope_id, status, created_at DESC)""")

    def _connect(self):
        db = sqlite3.connect(self.path, timeout=5.0)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=5000")
        return _ReviewConnection(db)

    @staticmethod
    def _row(row: sqlite3.Row) -> MemoryProposal:
        return MemoryProposal(*(row[key] for key in (
            "proposal_id","actor_id","scope_id","category","content","status",
            "created_at","updated_at","memory_id")))

    def propose(self, *, actor_id: str, scope_id: str, category: str,
                content: str) -> MemoryProposal:
        actor = _text(actor_id, "actor_id", 200)
        scope = _text(scope_id, "scope_id", 300)
        category = _text(category, "category", 40).lower()
        if category not in _CATEGORIES:
            raise ValueError("unsupported memory category")
        content = _text(content, "content", 12_000)
        now = _now()
        proposal_id = "ys_" + secrets.token_urlsafe(18)
        with self._connect() as db:
            db.execute("""INSERT INTO yuki_memory_proposals
                (proposal_id,actor_id,scope_id,category,content,status,created_at,updated_at,memory_id)
                VALUES (?,?,?,?,?,'pending',?,?,NULL)""",
                (proposal_id,actor,scope,category,content,now,now))
            row = db.execute("SELECT * FROM yuki_memory_proposals WHERE proposal_id=?",
                (proposal_id,)).fetchone()
            return self._row(row)

    def list(self, *, actor_id: str, scope_id: str,
             status: str = "pending", limit: int = 50) -> list[MemoryProposal]:
        actor = _text(actor_id, "actor_id", 200)
        scope = _text(scope_id, "scope_id", 300)
        if status not in {"pending","approved","rejected","all"}:
            raise ValueError("invalid proposal status")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 200:
            raise ValueError("limit must be between 1 and 200")
        sql = "SELECT * FROM yuki_memory_proposals WHERE actor_id=? AND scope_id=?"
        args: list[object] = [actor,scope]
        if status != "all":
            sql += " AND status=?"
            args.append(status)
        sql += " ORDER BY created_at DESC, proposal_id LIMIT ?"
        args.append(limit)
        with self._connect() as db:
            rows = db.execute(sql,args).fetchall()
        return [self._row(row) for row in rows]

    def approve(self, *, actor_id: str, scope_id: str,
                proposal_id: str) -> Memory:
        actor = _text(actor_id, "actor_id", 200)
        scope = _text(scope_id, "scope_id", 300)
        identifier = _text(proposal_id, "proposal_id", 128)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("""SELECT * FROM yuki_memory_proposals
                WHERE proposal_id=? AND actor_id=? AND scope_id=?""",
                (identifier,actor,scope)).fetchone()
            if row is None:
                raise KeyError("proposal not found")
            if row["status"] == "rejected":
                raise ValueError("rejected proposal cannot be approved")
            memory_id = row["memory_id"] or ("ym_" + secrets.token_urlsafe(18))
            # Reusing memory_id makes retries idempotent if the process stops
            # after the memory write but before the proposal status update.
            memory = self.memory_store.put(actor_id=actor,scope_id=scope,
                category=row["category"],content=row["content"],source="approved_suggestion",
                metadata={"proposal_id":identifier},memory_id=memory_id)
            db.execute("""UPDATE yuki_memory_proposals SET status='approved',
                memory_id=?,updated_at=? WHERE proposal_id=? AND actor_id=? AND scope_id=?""",
                (memory_id,_now(),identifier,actor,scope))
            return memory

    def reject(self, *, actor_id: str, scope_id: str, proposal_id: str) -> bool:
        actor = _text(actor_id, "actor_id", 200)
        scope = _text(scope_id, "scope_id", 300)
        identifier = _text(proposal_id, "proposal_id", 128)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("""SELECT status FROM yuki_memory_proposals
                WHERE proposal_id=? AND actor_id=? AND scope_id=?""",
                (identifier,actor,scope)).fetchone()
            if row is None:
                return False
            if row["status"] == "approved":
                raise ValueError("approved proposal cannot be rejected")
            if row["status"] == "rejected":
                return True
            db.execute("""UPDATE yuki_memory_proposals SET status='rejected',updated_at=?
                WHERE proposal_id=? AND actor_id=? AND scope_id=?""",
                (_now(),identifier,actor,scope))
            return True


class _ReviewConnection:
    def __init__(self, db):
        self.db = db
    def __enter__(self):
        return self.db
    def __exit__(self, exc_type, exc, tb):
        try:
            self.db.rollback() if exc_type else self.db.commit()
        finally:
            self.db.close()
        return False
