"""Tenant-scoped, versioned SQLite conversation persistence.

This store persists conversation data only. It does not authenticate callers;
the application must pass the authenticated principal's stable owner_id.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import sqlite3

from core.sqlite_file_security import prepare_private_sqlite_path
import time
import uuid
from typing import Any


MAX_OWNER_ID = 200
MAX_TITLE_CHARS = 160
MAX_USER_CHARS = 20_000
MAX_ASSISTANT_CHARS = 24_000
MAX_MESSAGES_PER_CONVERSATION = 5_000


class ConversationError(RuntimeError):
    """Base error for conversation persistence."""


class ConversationNotFound(ConversationError):
    """Conversation does not exist or is not owned by the requesting user."""


class ConversationConflict(ConversationError):
    """Optimistic version or idempotency conflict."""


class ConversationStore:
    """SQLite conversation store with owner scoping and optimistic concurrency."""

    def __init__(self, database_path: str | Path):
        if not isinstance(database_path, (str, Path)) or not str(database_path).strip():
            raise ValueError("database_path must be a non-empty path.")
        self.database_path = prepare_private_sqlite_path(database_path)
        with self._connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS conversations (
                    conversation_id TEXT PRIMARY KEY,
                    owner_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    version INTEGER NOT NULL DEFAULT 0 CHECK(version >= 0),
                    archived INTEGER NOT NULL DEFAULT 0 CHECK(archived IN (0, 1))
                );
                CREATE INDEX IF NOT EXISTS idx_conversations_owner_updated
                    ON conversations(owner_id, archived, updated_at DESC);
                CREATE TABLE IF NOT EXISTS conversation_messages (
                    message_id TEXT PRIMARY KEY,
                    conversation_id TEXT NOT NULL REFERENCES conversations(conversation_id) ON DELETE CASCADE,
                    sequence INTEGER NOT NULL CHECK(sequence > 0),
                    role TEXT NOT NULL CHECK(role IN ('user', 'assistant')),
                    content TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(conversation_id, sequence),
                    UNIQUE(conversation_id, idempotency_key)
                );
                CREATE INDEX IF NOT EXISTS idx_conversation_messages_order
                    ON conversation_messages(conversation_id, sequence);
                CREATE TABLE IF NOT EXISTS conversation_generation_leases (
                    conversation_id TEXT PRIMARY KEY REFERENCES conversations(conversation_id) ON DELETE CASCADE,
                    owner_id TEXT NOT NULL,
                    lease_token TEXT NOT NULL,
                    expires_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_generation_leases_expiry ON conversation_generation_leases(expires_at);

                CREATE TABLE IF NOT EXISTS conversation_generation_runs (
                    generation_id TEXT PRIMARY KEY,
                    conversation_id TEXT NOT NULL REFERENCES conversations(conversation_id) ON DELETE CASCADE,
                    owner_id TEXT NOT NULL,
                    request_id TEXT NOT NULL,
                    user_message_id TEXT NOT NULL UNIQUE REFERENCES conversation_messages(message_id) ON DELETE CASCADE,
                    status TEXT NOT NULL CHECK(status IN ('running', 'completed', 'interrupted', 'failed', 'cancelled')),
                    checkpoint TEXT NOT NULL DEFAULT '{}',
                    started_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    finished_at TEXT,
                    UNIQUE(conversation_id, request_id)
                );
                CREATE INDEX IF NOT EXISTS idx_generation_runs_owner_status
                    ON conversation_generation_runs(owner_id, status, updated_at);
            """)

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _owner(owner_id: str) -> str:
        if not isinstance(owner_id, str) or not owner_id.strip() or len(owner_id) > MAX_OWNER_ID:
            raise ValueError("owner_id must be non-empty text of at most 200 characters.")
        return owner_id.strip()

    @staticmethod
    def _conversation(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "conversation_id": row["conversation_id"],
            "owner_id": row["owner_id"],
            "title": row["title"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "version": row["version"],
            "archived": bool(row["archived"]),
        }

    @staticmethod
    def _message(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "message_id": row["message_id"],
            "conversation_id": row["conversation_id"],
            "sequence": row["sequence"],
            "role": row["role"],
            "content": row["content"],
            "created_at": row["created_at"],
        }

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.database_path, timeout=10, isolation_level=None)
        try:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("PRAGMA busy_timeout=10000")
            yield db
        finally:
            db.close()

    def create_conversation(self, *, owner_id: str, title: str = "New conversation") -> dict[str, Any]:
        owner = self._owner(owner_id)
        if not isinstance(title, str) or not title.strip() or len(title.strip()) > MAX_TITLE_CHARS:
            raise ValueError("title must be non-empty text of at most 160 characters.")
        now, conversation_id = self._now(), str(uuid.uuid4())
        with self._connect() as db:
            db.execute(
                """INSERT INTO conversations
                   (conversation_id, owner_id, title, created_at, updated_at, version, archived)
                   VALUES (?, ?, ?, ?, ?, 0, 0)""",
                (conversation_id, owner, title.strip(), now, now),
            )
            row = db.execute(
                "SELECT * FROM conversations WHERE conversation_id=?", (conversation_id,)
            ).fetchone()
        return self._conversation(row)

    def get_conversation(self, *, owner_id: str, conversation_id: str) -> dict[str, Any]:
        owner = self._owner(owner_id)
        if not isinstance(conversation_id, str) or not conversation_id.strip():
            raise ValueError("conversation_id must be non-empty text.")
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM conversations WHERE conversation_id=? AND owner_id=?",
                (conversation_id, owner),
            ).fetchone()
        if row is None:
            raise ConversationNotFound("conversation not found.")
        return self._conversation(row)

    def list_conversations(self, *, owner_id: str, limit: int = 50, include_archived: bool = False):
        owner = self._owner(owner_id)
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("limit must be an integer from 1 to 100.")
        with self._connect() as db:
            rows = db.execute(
                """SELECT * FROM conversations
                   WHERE owner_id=? AND (?=1 OR archived=0)
                   ORDER BY updated_at DESC, conversation_id LIMIT ?""",
                (owner, int(bool(include_archived)), limit),
            ).fetchall()
        return [self._conversation(row) for row in rows]

    def append_message(
        self, *, owner_id: str, conversation_id: str, role: str, content: str,
        idempotency_key: str, expected_version: int | None = None,
        generation_request_id: str | None = None,
        generation_lease_token: str | None = None,
    ) -> dict[str, Any]:
        owner = self._owner(owner_id)
        if not isinstance(role, str) or role not in {"user", "assistant"}:
            raise ValueError("role must be user or assistant.")
        maximum = MAX_USER_CHARS if role == "user" else MAX_ASSISTANT_CHARS
        if not isinstance(content, str) or not content.strip() or len(content) > maximum:
            raise ValueError(f"content must be non-empty text of at most {maximum} characters.")
        if not isinstance(idempotency_key, str) or not idempotency_key.strip() or len(idempotency_key) > 200:
            raise ValueError("idempotency_key must be non-empty text of at most 200 characters.")
        if generation_request_id is not None and (role != "user" or not isinstance(generation_request_id, str) or not generation_request_id.strip() or len(generation_request_id) > 160):
            raise ValueError("generation_request_id must be non-empty text for user messages.")
        if expected_version is not None and (type(expected_version) is not int or expected_version < 0):
            raise ValueError("expected_version must be a non-negative integer.")
        now, message_id = self._now(), str(uuid.uuid4())
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                conversation = db.execute(
                    "SELECT * FROM conversations WHERE conversation_id=? AND owner_id=?",
                    (conversation_id, owner),
                ).fetchone()
                if conversation is None:
                    raise ConversationNotFound("conversation not found.")
                if conversation["archived"]:
                    raise ConversationConflict("conversation is archived.")
                lease = db.execute(
                    """SELECT owner_id, lease_token, expires_at FROM conversation_generation_leases
                       WHERE conversation_id=?""",
                    (conversation_id,),
                ).fetchone()
                if lease is not None and (
                    generation_lease_token is None
                    or lease["owner_id"] != owner
                    or lease["lease_token"] != generation_lease_token
                    or lease["expires_at"] <= time.time()
                ):
                    raise ConversationConflict("generation lease is no longer owned by this worker.")
                previous = db.execute(
                    """SELECT * FROM conversation_messages
                       WHERE conversation_id=? AND idempotency_key=?""",
                    (conversation_id, idempotency_key.strip()),
                ).fetchone()
                if previous is not None:
                    if previous["role"] != role or previous["content"] != content:
                        raise ConversationConflict("idempotency key was reused with different message content.")
                    db.commit()
                    return {
                        "message": self._message(previous),
                        "version": conversation["version"],
                        "created": False,
                    }
                if expected_version is not None and conversation["version"] != expected_version:
                    raise ConversationConflict("conversation version changed; reload before retrying.")
                if conversation["version"] >= MAX_MESSAGES_PER_CONVERSATION:
                    raise ConversationConflict("conversation message limit reached.")
                sequence = conversation["version"] + 1
                db.execute(
                    """INSERT INTO conversation_messages
                       (message_id, conversation_id, sequence, role, content, idempotency_key, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (message_id, conversation_id, sequence, role, content, idempotency_key.strip(), now),
                )
                title = conversation["title"]
                if role == "user" and sequence == 1 and title == "New conversation":
                    title = " ".join(content.strip().split())[:MAX_TITLE_CHARS]
                db.execute(
                    """UPDATE conversations SET title=?, updated_at=?, version=?
                       WHERE conversation_id=? AND owner_id=?""",
                    (title, now, sequence, conversation_id, owner),
                )
                if generation_request_id is not None:
                    db.execute(
                        """INSERT INTO conversation_generation_runs
                           (generation_id, conversation_id, owner_id, request_id, user_message_id,
                            status, checkpoint, started_at, updated_at)
                           VALUES (?, ?, ?, ?, ?, 'running', '{}', ?, ?)""",
                        (str(uuid.uuid4()), conversation_id, owner, generation_request_id.strip(),
                         message_id, now, now),
                    )
                row = db.execute(
                    "SELECT * FROM conversation_messages WHERE message_id=?", (message_id,)
                ).fetchone()
                db.commit()
                return {"message": self._message(row), "version": sequence, "created": True}
            except Exception:
                db.rollback()
                raise

    def list_messages(
        self, *, owner_id: str, conversation_id: str, limit: int = 100,
        after_sequence: int | None = None,
    ) -> list[dict[str, Any]]:
        owner = self._owner(owner_id)
        if type(limit) is not int or not 1 <= limit <= 200:
            raise ValueError("limit must be an integer from 1 to 200.")
        if after_sequence is not None and (type(after_sequence) is not int or after_sequence < 0):
            raise ValueError("after_sequence must be a non-negative integer.")
        with self._connect() as db:
            exists = db.execute(
                "SELECT 1 FROM conversations WHERE conversation_id=? AND owner_id=?",
                (conversation_id, owner),
            ).fetchone()
            if exists is None:
                raise ConversationNotFound("conversation not found.")
            if after_sequence is None:
                rows = db.execute(
                    """SELECT * FROM (
                           SELECT * FROM conversation_messages
                           WHERE conversation_id=? ORDER BY sequence DESC LIMIT ?
                       ) ORDER BY sequence ASC""",
                    (conversation_id, limit),
                ).fetchall()
            else:
                rows = db.execute(
                    """SELECT * FROM conversation_messages
                       WHERE conversation_id=? AND sequence>? ORDER BY sequence ASC LIMIT ?""",
                    (conversation_id, after_sequence, limit),
                ).fetchall()
        return [self._message(row) for row in rows]


    def acquire_generation_lease(self, *, owner_id: str, conversation_id: str, lease_token: str, ttl_seconds: int = 600) -> dict[str, Any]:
        """Atomically claim one conversation's generation slot across processes."""
        owner = self._owner(owner_id)
        if not isinstance(conversation_id, str) or not conversation_id.strip():
            raise ValueError("conversation_id must be non-empty text.")
        if not isinstance(lease_token, str) or not lease_token.strip() or len(lease_token) > 200:
            raise ValueError("lease_token must be non-empty text of at most 200 characters.")
        if type(ttl_seconds) is not int or not 5 <= ttl_seconds <= 3600:
            raise ValueError("ttl_seconds must be an integer from 5 to 3600.")
        now = time.time()
        expires_at = now + ttl_seconds
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                conversation = db.execute(
                    "SELECT archived FROM conversations WHERE conversation_id=? AND owner_id=?",
                    (conversation_id, owner),
                ).fetchone()
                if conversation is None:
                    raise ConversationNotFound("conversation not found.")
                if conversation["archived"]:
                    raise ConversationConflict("conversation is archived.")
                current = db.execute(
                    "SELECT expires_at FROM conversation_generation_leases WHERE conversation_id=?",
                    (conversation_id,),
                ).fetchone()
                if current is not None and current["expires_at"] > now:
                    raise ConversationConflict("another generation is already active for this conversation.")
                db.execute(
                    """INSERT INTO conversation_generation_leases
                       (conversation_id, owner_id, lease_token, expires_at) VALUES (?, ?, ?, ?)
                       ON CONFLICT(conversation_id) DO UPDATE SET
                         owner_id=excluded.owner_id, lease_token=excluded.lease_token,
                         expires_at=excluded.expires_at""",
                    (conversation_id, owner, lease_token.strip(), expires_at),
                )
                db.commit()
            except Exception:
                db.rollback()
                raise
        return {"conversation_id": conversation_id, "expires_at": expires_at}

    def renew_generation_lease(self, *, owner_id: str, conversation_id: str, lease_token: str, ttl_seconds: int = 600) -> bool:
        """Extend an active lease only while its current fencing token is valid."""
        owner = self._owner(owner_id)
        if not isinstance(lease_token, str) or not lease_token.strip():
            raise ValueError("lease_token must be non-empty text.")
        if type(ttl_seconds) is not int or not 5 <= ttl_seconds <= 3600:
            raise ValueError("ttl_seconds must be an integer from 5 to 3600.")
        now = time.time()
        with self._connect() as db:
            cursor = db.execute(
                """UPDATE conversation_generation_leases SET expires_at=?
                   WHERE conversation_id=? AND owner_id=? AND lease_token=? AND expires_at>?""",
                (now + ttl_seconds, conversation_id, owner, lease_token.strip(), now),
            )
            return cursor.rowcount == 1

    def release_generation_lease(self, *, owner_id: str, conversation_id: str, lease_token: str) -> bool:
        """Release only a lease matching its owner and unique fencing token."""
        owner = self._owner(owner_id)
        if not isinstance(lease_token, str) or not lease_token.strip():
            raise ValueError("lease_token must be non-empty text.")
        with self._connect() as db:
            cursor = db.execute(
                """DELETE FROM conversation_generation_leases
                   WHERE conversation_id=? AND owner_id=? AND lease_token=?""",
                (conversation_id, owner, lease_token.strip()),
            )
            return cursor.rowcount == 1


    def update_generation_checkpoint(
        self, *, owner_id: str, conversation_id: str, request_id: str,
        checkpoint: dict[str, Any], lease_token: str | None = None,
    ) -> bool:
        """Persist a bounded, non-sensitive execution checkpoint for recovery."""
        owner = self._owner(owner_id)
        if not isinstance(request_id, str) or not request_id.strip() or len(request_id) > 160:
            raise ValueError("request_id must be non-empty text of at most 160 characters.")
        if not isinstance(checkpoint, dict) or len(checkpoint) > 16:
            raise ValueError("checkpoint must be a small object.")
        import json
        encoded = json.dumps(checkpoint, ensure_ascii=True, separators=(",", ":"))
        if len(encoded) > 4096:
            raise ValueError("checkpoint exceeds 4096 characters.")
        with self._connect() as db:
            if lease_token is None:
                cursor = db.execute(
                    """UPDATE conversation_generation_runs SET checkpoint=?, updated_at=?
                       WHERE owner_id=? AND conversation_id=? AND request_id=? AND status='running'
                         AND NOT EXISTS (SELECT 1 FROM conversation_generation_leases l
                           WHERE l.conversation_id=?)""",
                    (encoded, self._now(), owner, conversation_id, request_id.strip(), conversation_id),
                )
            else:
                cursor = db.execute(
                    """UPDATE conversation_generation_runs SET checkpoint=?, updated_at=?
                       WHERE owner_id=? AND conversation_id=? AND request_id=? AND status='running'
                         AND EXISTS (SELECT 1 FROM conversation_generation_leases l
                           WHERE l.conversation_id=? AND l.owner_id=? AND l.lease_token=? AND l.expires_at>?)""",
                    (encoded, self._now(), owner, conversation_id, request_id.strip(),
                     conversation_id, owner, lease_token, time.time()),
                )
            return cursor.rowcount == 1

    def finish_generation(
        self, *, owner_id: str, conversation_id: str, request_id: str,
        status: str, lease_token: str | None = None,
    ) -> bool:
        owner = self._owner(owner_id)
        if status not in {"completed", "interrupted", "failed", "cancelled"}:
            raise ValueError("invalid generation terminal status.")
        now = self._now()
        with self._connect() as db:
            if lease_token is None:
                cursor = db.execute(
                    """UPDATE conversation_generation_runs SET status=?, updated_at=?, finished_at=?
                       WHERE owner_id=? AND conversation_id=? AND request_id=? AND status='running'
                         AND NOT EXISTS (SELECT 1 FROM conversation_generation_leases l
                           WHERE l.conversation_id=?)""",
                    (status, now, now, owner, conversation_id, request_id.strip(), conversation_id),
                )
            else:
                cursor = db.execute(
                    """UPDATE conversation_generation_runs SET status=?, updated_at=?, finished_at=?
                       WHERE owner_id=? AND conversation_id=? AND request_id=? AND status='running'
                         AND EXISTS (SELECT 1 FROM conversation_generation_leases l
                           WHERE l.conversation_id=? AND l.owner_id=? AND l.lease_token=? AND l.expires_at>?)""",
                    (status, now, now, owner, conversation_id, request_id.strip(),
                     conversation_id, owner, lease_token, time.time()),
                )
            return cursor.rowcount == 1

    def list_interrupted_generations(
        self, *, owner_id: str, conversation_id: str | None = None, limit: int = 100,
    ) -> list[dict[str, Any]]:
        owner = self._owner(owner_id)
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("limit must be an integer from 1 to 100.")
        with self._connect() as db:
            if conversation_id is None:
                rows = db.execute(
                    """SELECT * FROM conversation_generation_runs
                       WHERE owner_id=? AND status='running'
                       ORDER BY started_at LIMIT ?""", (owner, limit)
                ).fetchall()
            else:
                rows = db.execute(
                    """SELECT * FROM conversation_generation_runs
                       WHERE owner_id=? AND conversation_id=? AND status='running'
                       ORDER BY started_at LIMIT ?""", (owner, conversation_id, limit)
                ).fetchall()
        import json
        return [{
            "generation_id": row["generation_id"], "conversation_id": row["conversation_id"],
            "request_id": row["request_id"], "user_message_id": row["user_message_id"],
            "status": row["status"], "checkpoint": json.loads(row["checkpoint"]),
            "started_at": row["started_at"], "updated_at": row["updated_at"],
        } for row in rows]

    def archive_conversation(self, *, owner_id: str, conversation_id: str) -> dict[str, Any]:
        owner = self._owner(owner_id)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            cursor = db.execute(
                "UPDATE conversations SET archived=1, updated_at=? WHERE conversation_id=? AND owner_id=?",
                (self._now(), conversation_id, owner),
            )
            if cursor.rowcount != 1:
                db.rollback()
                raise ConversationNotFound("conversation not found.")
            row = db.execute(
                "SELECT * FROM conversations WHERE conversation_id=?", (conversation_id,)
            ).fetchone()
            db.commit()
        return self._conversation(row)
