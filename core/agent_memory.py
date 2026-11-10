"""Permission-aware persistent memory for Yuki specialist agents.

Memories are scoped to an actor and agent by default. Retrieved content is
untrusted reference data, never executable instructions. Sharing requires a
separate authorization decision. This module performs no model calls.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
import sqlite3
from threading import RLock
from uuid import uuid4

_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:@/-]{0,199}$")
_CATEGORIES = frozenset({"working", "episodic", "semantic", "procedural"})
_MAX_TEXT = 8000
_MAX_SOURCE = 500
_MAX_LIMIT = 100


class AgentMemoryStore:
    def __init__(self, database_path: str, *, authorize):
        if not isinstance(database_path, str) or not database_path.strip():
            raise ValueError("database_path_required")
        if not callable(authorize):
            raise TypeError("trusted_authorize_callback_required")
        self.database_path = str(Path(database_path).expanduser())
        Path(self.database_path).parent.mkdir(parents=True, exist_ok=True)
        self.authorize = authorize
        self._lock = RLock()
        with self._connect() as db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS agent_memories (
                    memory_id TEXT PRIMARY KEY,
                    actor_id TEXT NOT NULL,
                    agent_id TEXT NOT NULL,
                    category TEXT NOT NULL,
                    content TEXT NOT NULL,
                    source TEXT NOT NULL,
                    confidence REAL NOT NULL CHECK(confidence >= 0 AND confidence <= 1),
                    verified INTEGER NOT NULL CHECK(verified IN (0, 1)),
                    scope TEXT NOT NULL CHECK(scope IN ('private', 'shared')),
                    created_at TEXT NOT NULL,
                    expires_at TEXT,
                    superseded_by TEXT,
                    superseded_at TEXT,
                    lifecycle_status TEXT NOT NULL DEFAULT 'active',
                    lifecycle_updated_at TEXT,
                    lifecycle_reason TEXT
                )
            """)
            columns = {row[1] for row in db.execute("PRAGMA table_info(agent_memories)").fetchall()}
            if "superseded_by" not in columns:
                db.execute("ALTER TABLE agent_memories ADD COLUMN superseded_by TEXT")
            if "superseded_at" not in columns:
                db.execute("ALTER TABLE agent_memories ADD COLUMN superseded_at TEXT")
            if "lifecycle_status" not in columns:
                db.execute("ALTER TABLE agent_memories ADD COLUMN lifecycle_status TEXT NOT NULL DEFAULT 'active'")
            if "lifecycle_updated_at" not in columns:
                db.execute("ALTER TABLE agent_memories ADD COLUMN lifecycle_updated_at TEXT")
            if "lifecycle_reason" not in columns:
                db.execute("ALTER TABLE agent_memories ADD COLUMN lifecycle_reason TEXT")
            db.execute("""CREATE TABLE IF NOT EXISTS agent_memory_lifecycle_events (
                event_id TEXT PRIMARY KEY,
                actor_id TEXT NOT NULL,
                agent_id TEXT NOT NULL,
                memory_id TEXT NOT NULL,
                previous_status TEXT NOT NULL,
                new_status TEXT NOT NULL,
                reason TEXT NOT NULL,
                occurred_at TEXT NOT NULL
            )""")
            db.execute("""CREATE INDEX IF NOT EXISTS idx_memory_lifecycle_history
                ON agent_memory_lifecycle_events(actor_id, agent_id, memory_id, occurred_at)""")
            db.execute("""CREATE TRIGGER IF NOT EXISTS trg_memory_lifecycle_no_update
                BEFORE UPDATE ON agent_memory_lifecycle_events
                BEGIN SELECT RAISE(ABORT, 'memory_lifecycle_audit_is_immutable'); END""")
            db.execute("""CREATE TRIGGER IF NOT EXISTS trg_memory_lifecycle_no_delete
                BEFORE DELETE ON agent_memory_lifecycle_events
                BEGIN SELECT RAISE(ABORT, 'memory_lifecycle_audit_is_immutable'); END""")
            db.execute("""CREATE TABLE IF NOT EXISTS agent_memory_corrections (
                correction_id TEXT PRIMARY KEY,
                actor_id TEXT NOT NULL,
                agent_id TEXT NOT NULL,
                previous_memory_id TEXT NOT NULL,
                replacement_memory_id TEXT NOT NULL,
                reason TEXT NOT NULL,
                approved_at TEXT NOT NULL
            )""")
            db.execute("""CREATE INDEX IF NOT EXISTS idx_memory_corrections_previous
                ON agent_memory_corrections(actor_id, previous_memory_id, approved_at)""")
            db.execute("""CREATE TRIGGER IF NOT EXISTS trg_memory_corrections_no_update
                BEFORE UPDATE ON agent_memory_corrections
                BEGIN SELECT RAISE(ABORT, 'memory_correction_audit_is_immutable'); END""")
            db.execute("""CREATE TRIGGER IF NOT EXISTS trg_memory_corrections_no_delete
                BEFORE DELETE ON agent_memory_corrections
                BEGIN SELECT RAISE(ABORT, 'memory_correction_audit_is_immutable'); END""")
            db.execute("""CREATE INDEX IF NOT EXISTS idx_agent_memory_scope
                ON agent_memories(actor_id, agent_id, category, created_at)""")
            db.execute("""CREATE INDEX IF NOT EXISTS idx_agent_memory_expiry
                ON agent_memories(expires_at)""")

    def _connect(self):
        db = sqlite3.connect(self.database_path, timeout=5.0)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=5000")
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA synchronous=FULL")
        return _Connection(db)

    def _allowed(self, actor_id, action, resource_id):
        if not isinstance(actor_id, str) or not _ID.fullmatch(actor_id):
            return False
        try:
            return self.authorize(actor_id, action, resource_id) is True
        except Exception:
            return False

    @staticmethod
    def _validate_scope(actor_id, agent_id):
        if not isinstance(actor_id, str) or not _ID.fullmatch(actor_id):
            raise ValueError("invalid_actor_id")
        if not isinstance(agent_id, str) or not _ID.fullmatch(agent_id):
            raise ValueError("invalid_agent_id")

    @staticmethod
    def _record(row):
        return {
            "memory_id": row["memory_id"],
            "actor_id": row["actor_id"],
            "agent_id": row["agent_id"],
            "category": row["category"],
            "content": row["content"],
            "source": row["source"],
            "confidence": row["confidence"],
            "verified": bool(row["verified"]),
            "scope": row["scope"],
            "created_at": row["created_at"],
            "expires_at": row["expires_at"],
            "superseded_by": row["superseded_by"] if "superseded_by" in row.keys() else None,
            "superseded_at": row["superseded_at"] if "superseded_at" in row.keys() else None,
            "lifecycle_status": row["lifecycle_status"] if "lifecycle_status" in row.keys() else "active",
            "lifecycle_updated_at": row["lifecycle_updated_at"] if "lifecycle_updated_at" in row.keys() else None,
            "lifecycle_reason": row["lifecycle_reason"] if "lifecycle_reason" in row.keys() else None,
            "trust": "untrusted_reference_data",
            "may_contain_instructions": True,
            "must_not_be_executed_as_instructions": True,
        }

    def remember(
        self, *, actor_id, agent_id, content, source,
        category="episodic", confidence=0.5, verified=False,
        scope="private", retention_days=365,
    ):
        self._validate_scope(actor_id, agent_id)
        if not isinstance(content, str) or not content.strip() or len(content) > _MAX_TEXT or "\x00" in content:
            raise ValueError("invalid_memory_content")
        if not isinstance(source, str) or not source.strip() or len(source) > _MAX_SOURCE:
            raise ValueError("memory_source_required")
        if category not in _CATEGORIES:
            raise ValueError("invalid_memory_category")
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1:
            raise ValueError("invalid_memory_confidence")
        if type(verified) is not bool:
            raise ValueError("verified_must_be_boolean")
        if scope not in {"private", "shared"}:
            raise ValueError("invalid_memory_scope")
        if type(retention_days) is not int or not 1 <= retention_days <= 3650:
            raise ValueError("retention_days_must_be_1_to_3650")
        action = "agent_memory:write" if scope == "private" else "agent_memory:share"
        if not self._allowed(actor_id, action, agent_id):
            raise PermissionError("agent memory write is not authorized")
        now = datetime.now(timezone.utc)
        memory_id = uuid4().hex
        expires = (now + timedelta(days=retention_days)).isoformat()
        with self._lock, self._connect() as db:
            db.execute("""INSERT INTO agent_memories
                (memory_id,actor_id,agent_id,category,content,source,confidence,
                 verified,scope,created_at,expires_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (memory_id, actor_id, agent_id, category, content.strip(), source.strip(),
                 float(confidence), int(verified), scope, now.isoformat(), expires))
            row = db.execute("SELECT * FROM agent_memories WHERE memory_id=?", (memory_id,)).fetchone()
        return self._record(row)

    def list_memories(self, *, actor_id, agent_id, limit=50, include_shared=True):
        self._validate_scope(actor_id, agent_id)
        if not self._allowed(actor_id, "agent_memory:read", agent_id):
            raise PermissionError("agent memory read is not authorized")
        if type(limit) is not int or not 1 <= limit <= _MAX_LIMIT:
            raise ValueError("limit_must_be_1_to_100")
        if type(include_shared) is not bool:
            raise ValueError("include_shared_must_be_boolean")
        shared_visible = include_shared and self._allowed(
            actor_id, "agent_memory:share", agent_id
        )
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as db:
            rows = db.execute("""SELECT * FROM agent_memories
                WHERE actor_id=? AND ((scope='private' AND agent_id=?) OR (scope='shared' AND ?=1))
                AND (expires_at IS NULL OR expires_at>?)
                AND superseded_by IS NULL
                AND lifecycle_status NOT IN ('rejected', 'retired')
                ORDER BY created_at DESC LIMIT ?""",
                (actor_id, agent_id, int(shared_visible), now, limit)).fetchall()
        return [self._record(row) for row in rows]

    def list_memories_page(self, *, actor_id, agent_id, limit=100,
                           include_shared=True, before=None):
        """Authorized keyset page for resumable maintenance operations.

        Cursor is the last record's (created_at, memory_id), ordered newest
        first. Newer inserts do not shift an in-progress older-page scan.
        """
        self._validate_scope(actor_id, agent_id)
        if not self._allowed(actor_id, "agent_memory:read", agent_id):
            raise PermissionError("agent memory read is not authorized")
        if type(limit) is not int or not 1 <= limit <= _MAX_LIMIT:
            raise ValueError("limit_must_be_1_to_100")
        if type(include_shared) is not bool:
            raise ValueError("include_shared_must_be_boolean")
        cursor_created = cursor_id = None
        if before is not None:
            if not isinstance(before, dict):
                raise ValueError("invalid_memory_page_cursor")
            cursor_created, cursor_id = before.get("created_at"), before.get("memory_id")
            if (not isinstance(cursor_created, str) or len(cursor_created) > 64
                    or not isinstance(cursor_id, str)
                    or not re.fullmatch(r"[a-f0-9]{32}", cursor_id)):
                raise ValueError("invalid_memory_page_cursor")
        shared_visible = include_shared and self._allowed(
            actor_id, "agent_memory:share", agent_id
        )
        now = datetime.now(timezone.utc).isoformat()
        sql = """SELECT * FROM agent_memories
            WHERE actor_id=? AND ((scope='private' AND agent_id=?) OR (scope='shared' AND ?=1))
            AND (expires_at IS NULL OR expires_at>?)
            AND superseded_by IS NULL
            AND lifecycle_status NOT IN ('rejected', 'retired')"""

        params = [actor_id, agent_id, int(shared_visible), now]
        if before is not None:
            sql += " AND (created_at < ? OR (created_at = ? AND memory_id < ?))"
            params.extend([cursor_created, cursor_created, cursor_id])
        sql += " ORDER BY created_at DESC, memory_id DESC LIMIT ?"
        params.append(limit)
        with self._connect() as db:
            rows = db.execute(sql, params).fetchall()
        return [self._record(row) for row in rows]

    def search(self, *, actor_id, agent_id, query, limit=10, include_shared=True):
        if not isinstance(query, str) or not query.strip() or len(query) > 1000:
            raise ValueError("invalid_memory_query")
        if type(limit) is not int or not 1 <= limit <= 20:
            raise ValueError("limit_must_be_1_to_20")
        rows = self.list_memories(actor_id=actor_id, agent_id=agent_id,
                                 limit=_MAX_LIMIT, include_shared=include_shared)
        terms = set(re.findall(r"[a-z0-9_]{2,}", query.lower()))
        ranked = []
        for row in rows:
            tokens = set(re.findall(r"[a-z0-9_]{2,}", row["content"].lower()))
            overlap = len(terms & tokens)
            if overlap:
                ranked.append((overlap / max(1, len(terms | tokens)), row))
        ranked.sort(key=lambda item: (item[0], item[1]["created_at"]), reverse=True)
        return [{"score": round(score, 4), **row} for score, row in ranked[:limit]]

    @staticmethod
    def _delete_embedding_cache_rows(db, *, actor_id, memory_ids):
        """Delete derived vectors in the same transaction as source memories."""
        if not memory_ids:
            return 0
        table = db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='agent_memory_embeddings'"
        ).fetchone()
        if table is None:
            return 0
        placeholders = ",".join("?" for _ in memory_ids)
        cursor = db.execute(
            f"DELETE FROM agent_memory_embeddings WHERE actor_id=? AND memory_id IN ({placeholders})",
            (actor_id, *memory_ids),
        )
        return cursor.rowcount


    def correct(self, *, actor_id, agent_id, memory_id, content, source,
                reason, confidence=1.0, retention_days=None):
        """Owner-authorized, audited correction; preserve the superseded claim."""
        self._validate_scope(actor_id, agent_id)
        if not isinstance(memory_id, str) or not re.fullmatch(r"[a-f0-9]{32}", memory_id):
            raise ValueError("invalid_memory_id")
        if not isinstance(content, str) or not content.strip() or len(content) > _MAX_TEXT or "\x00" in content:
            raise ValueError("invalid_memory_content")
        if not isinstance(source, str) or not source.strip() or len(source) > _MAX_SOURCE:
            raise ValueError("memory_source_required")
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 1000 or "\x00" in reason:
            raise ValueError("correction_reason_required")
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1:
            raise ValueError("invalid_memory_confidence")
        if retention_days is not None and (type(retention_days) is not int or not 1 <= retention_days <= 3650):
            raise ValueError("retention_days_must_be_1_to_3650")
        if not self._allowed(actor_id, "agent_memory:correct", agent_id):
            raise PermissionError("agent memory correction is not authorized")

        now = datetime.now(timezone.utc).isoformat()
        replacement_id = uuid4().hex
        correction_id = uuid4().hex
        with self._lock, self._connect() as db:
            previous = db.execute(
                """SELECT * FROM agent_memories
                   WHERE memory_id=? AND actor_id=? AND agent_id=?
                   AND superseded_by IS NULL""",
                (memory_id, actor_id, agent_id),
            ).fetchone()
            if previous is None:
                raise ValueError("active_memory_not_found")
            if previous["scope"] == "shared" and not self._allowed(
                actor_id, "agent_memory:share", agent_id
            ):
                raise PermissionError("shared memory correction is not authorized")
            expires_at = previous["expires_at"]
            if retention_days is not None:
                expires_at = (datetime.now(timezone.utc) + timedelta(days=retention_days)).isoformat()
            cursor = db.execute(
                """UPDATE agent_memories SET superseded_by=?, superseded_at=?
                   WHERE memory_id=? AND actor_id=? AND agent_id=? AND superseded_by IS NULL""",
                (replacement_id, now, memory_id, actor_id, agent_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("active_memory_not_found")
            db.execute(
                """INSERT INTO agent_memories
                   (memory_id,actor_id,agent_id,category,content,source,confidence,
                    verified,scope,created_at,expires_at,superseded_by,superseded_at,
                    lifecycle_status,lifecycle_updated_at,lifecycle_reason)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (replacement_id, actor_id, agent_id, previous["category"],
                 content.strip(), source.strip(), float(confidence), 1,
                 previous["scope"], now, expires_at, None, None,
                 "approved", now, reason.strip()),
            )
            db.execute(
                """INSERT INTO agent_memory_corrections
                   (correction_id,actor_id,agent_id,previous_memory_id,
                    replacement_memory_id,reason,approved_at)
                   VALUES(?,?,?,?,?,?,?)""",
                (correction_id, actor_id, agent_id, memory_id, replacement_id,
                 reason.strip(), now),
            )
            self._delete_embedding_cache_rows(
                db, actor_id=actor_id, memory_ids=[memory_id]
            )
            row = db.execute(
                "SELECT * FROM agent_memories WHERE memory_id=?",
                (replacement_id,),
            ).fetchone()
        return self._record(row)

    def correction_history(self, *, actor_id, agent_id, memory_id):
        """Return immutable correction audit events for an authorized owner."""
        self._validate_scope(actor_id, agent_id)
        if not isinstance(memory_id, str) or not re.fullmatch(r"[a-f0-9]{32}", memory_id):
            raise ValueError("invalid_memory_id")
        if not self._allowed(actor_id, "agent_memory:correct", agent_id):
            raise PermissionError("agent memory correction history is not authorized")
        with self._connect() as db:
            rows = db.execute(
                """SELECT * FROM agent_memory_corrections
                   WHERE actor_id=? AND agent_id=?
                   AND (previous_memory_id=? OR replacement_memory_id=?)
                   ORDER BY approved_at ASC, correction_id ASC""",
                (actor_id, agent_id, memory_id, memory_id),
            ).fetchall()
        return [{
            "correction_id": row["correction_id"],
            "actor_id": row["actor_id"],
            "agent_id": row["agent_id"],
            "previous_memory_id": row["previous_memory_id"],
            "replacement_memory_id": row["replacement_memory_id"],
            "reason": row["reason"],
            "approved_at": row["approved_at"],
        } for row in rows]


    def transition_lifecycle(self, *, actor_id, agent_id, memory_id,
                             status, reason):
        """Owner-authorized approval/rejection/retirement with immutable history."""
        self._validate_scope(actor_id, agent_id)
        if not isinstance(memory_id, str) or not re.fullmatch(r"[a-f0-9]{32}", memory_id):
            raise ValueError("invalid_memory_id")
        if status not in {"approved", "rejected", "retired"}:
            raise ValueError("invalid_lifecycle_status")
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 1000 or "\x00" in reason:
            raise ValueError("lifecycle_reason_required")
        permission = "agent_memory:retire" if status == "retired" else "agent_memory:review"
        if not self._allowed(actor_id, permission, agent_id):
            raise PermissionError("memory lifecycle transition is not authorized")
        now = datetime.now(timezone.utc).isoformat()
        event_id = uuid4().hex
        with self._lock, self._connect() as db:
            row = db.execute(
                """SELECT * FROM agent_memories WHERE memory_id=?
                   AND actor_id=? AND agent_id=? AND superseded_by IS NULL""",
                (memory_id, actor_id, agent_id),
            ).fetchone()
            if row is None:
                raise ValueError("active_memory_not_found")
            previous = row["lifecycle_status"]
            if previous == status:
                raise ValueError("memory_already_in_requested_status")
            if previous in {"rejected", "retired"}:
                raise ValueError("terminal_memory_status_requires_new_memory")
            db.execute(
                """UPDATE agent_memories SET lifecycle_status=?,
                   lifecycle_updated_at=?, lifecycle_reason=?, verified=?
                   WHERE memory_id=? AND actor_id=? AND agent_id=?""",
                (status, now, reason.strip(), 1 if status == "approved" else row["verified"],
                 memory_id, actor_id, agent_id),
            )
            db.execute(
                """INSERT INTO agent_memory_lifecycle_events
                   (event_id,actor_id,agent_id,memory_id,previous_status,
                    new_status,reason,occurred_at) VALUES(?,?,?,?,?,?,?,?)""",
                (event_id, actor_id, agent_id, memory_id, previous, status,
                 reason.strip(), now),
            )
            updated = db.execute(
                "SELECT * FROM agent_memories WHERE memory_id=?", (memory_id,)
            ).fetchone()
        return self._record(updated)

    def renew(self, *, actor_id, agent_id, memory_id, retention_days, reason):
        """Extend expiry of an active/approved claim with a recorded rationale."""
        self._validate_scope(actor_id, agent_id)
        if not isinstance(memory_id, str) or not re.fullmatch(r"[a-f0-9]{32}", memory_id):
            raise ValueError("invalid_memory_id")
        if type(retention_days) is not int or not 1 <= retention_days <= 3650:
            raise ValueError("retention_days_must_be_1_to_3650")
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 1000 or "\x00" in reason:
            raise ValueError("renewal_reason_required")
        if not self._allowed(actor_id, "agent_memory:renew", agent_id):
            raise PermissionError("memory renewal is not authorized")
        now = datetime.now(timezone.utc)
        event_id = uuid4().hex
        with self._lock, self._connect() as db:
            row = db.execute(
                """SELECT * FROM agent_memories WHERE memory_id=?
                   AND actor_id=? AND agent_id=? AND superseded_by IS NULL""",
                (memory_id, actor_id, agent_id),
            ).fetchone()
            if row is None:
                raise ValueError("active_memory_not_found")
            if row["lifecycle_status"] in {"rejected", "retired"}:
                raise ValueError("terminal_memory_status_cannot_be_renewed")
            previous = row["lifecycle_status"]
            # Retention is an extension, not a replacement lifetime: never
            # shorten an existing expiry when the owner renews a record.
            expiry_base = (
                datetime.fromisoformat(row["expires_at"])
                if row["expires_at"] is not None else now
            )
            expires = (max(expiry_base, now) + timedelta(days=retention_days)).isoformat()
            db.execute(
                """UPDATE agent_memories SET expires_at=?,
                   lifecycle_updated_at=?, lifecycle_reason=?
                   WHERE memory_id=? AND actor_id=? AND agent_id=?""",
                (expires, now.isoformat(), reason.strip(), memory_id, actor_id, agent_id),
            )
            db.execute(
                """INSERT INTO agent_memory_lifecycle_events
                   (event_id,actor_id,agent_id,memory_id,previous_status,
                    new_status,reason,occurred_at) VALUES(?,?,?,?,?,?,?,?)""",
                (event_id, actor_id, agent_id, memory_id, previous, previous,
                 "renewal: " + reason.strip(), now.isoformat()),
            )
            updated = db.execute(
                "SELECT * FROM agent_memories WHERE memory_id=?", (memory_id,)
            ).fetchone()
        return self._record(updated)

    def lifecycle_history(self, *, actor_id, agent_id, memory_id):
        self._validate_scope(actor_id, agent_id)
        if not isinstance(memory_id, str) or not re.fullmatch(r"[a-f0-9]{32}", memory_id):
            raise ValueError("invalid_memory_id")
        if not self._allowed(actor_id, "agent_memory:review", agent_id):
            raise PermissionError("memory lifecycle history is not authorized")
        with self._connect() as db:
            rows = db.execute(
                """SELECT * FROM agent_memory_lifecycle_events
                   WHERE actor_id=? AND agent_id=? AND memory_id=?
                   ORDER BY occurred_at ASC, event_id ASC""",
                (actor_id, agent_id, memory_id),
            ).fetchall()
        return [{
            "event_id": row["event_id"], "memory_id": row["memory_id"],
            "previous_status": row["previous_status"], "new_status": row["new_status"],
            "reason": row["reason"], "occurred_at": row["occurred_at"],
        } for row in rows]

    def delete(self, *, actor_id, agent_id, memory_id):
        self._validate_scope(actor_id, agent_id)
        if not isinstance(memory_id, str) or not re.fullmatch(r"[a-f0-9]{32}", memory_id):
            raise ValueError("invalid_memory_id")
        if not self._allowed(actor_id, "agent_memory:delete", agent_id):
            raise PermissionError("agent memory delete is not authorized")
        with self._lock, self._connect() as db:
            cursor = db.execute("""DELETE FROM agent_memories
                WHERE memory_id=? AND actor_id=? AND agent_id=?""",
                (memory_id, actor_id, agent_id))
            if cursor.rowcount == 1:
                self._delete_embedding_cache_rows(db, actor_id=actor_id, memory_ids=[memory_id])
            return cursor.rowcount == 1

    def prune_expired(self, *, actor_id):
        if not self._allowed(actor_id, "agent_memory:prune", ""):
            raise PermissionError("memory pruning is not authorized")
        now = datetime.now(timezone.utc).isoformat()
        with self._lock, self._connect() as db:
            expired = db.execute(
                """SELECT memory_id FROM agent_memories
                   WHERE actor_id=? AND expires_at IS NOT NULL AND expires_at<=?""",
                (actor_id, now),
            ).fetchall()
            memory_ids = [row["memory_id"] for row in expired]
            if not memory_ids:
                return 0
            placeholders = ",".join("?" for _ in memory_ids)
            cursor = db.execute(
                f"DELETE FROM agent_memories WHERE actor_id=? AND memory_id IN ({placeholders})",
                (actor_id, *memory_ids),
            )
            if cursor.rowcount:
                self._delete_embedding_cache_rows(
                    db, actor_id=actor_id, memory_ids=memory_ids
                )
            return cursor.rowcount


class _Connection:
    def __init__(self, db):
        self.db = db
    def __enter__(self):
        return self.db
    def __exit__(self, exc_type, exc, tb):
        try:
            self.db.commit() if exc_type is None else self.db.rollback()
        finally:
            self.db.close()
        return False
