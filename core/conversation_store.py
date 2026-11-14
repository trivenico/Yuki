"""Tenant-scoped, versioned SQLite conversation persistence.

This store persists conversation data only. It does not authenticate callers;
the application must pass the authenticated principal's stable owner_id.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import hashlib
import json

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
                CREATE TABLE IF NOT EXISTS conversation_tool_executions (
                    execution_id TEXT PRIMARY KEY,
                    conversation_id TEXT NOT NULL REFERENCES conversations(conversation_id) ON DELETE CASCADE,
                    owner_id TEXT NOT NULL,
                    request_id TEXT NOT NULL,
                    call_id TEXT NOT NULL,
                    tool_name TEXT NOT NULL,
                    arguments_sha256 TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    provider_idempotency INTEGER NOT NULL DEFAULT 0 CHECK(provider_idempotency IN (0, 1)),
                    lease_token TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('started', 'completed', 'failed', 'uncertain')),
                    started_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(conversation_id, request_id, call_id),
                    FOREIGN KEY(conversation_id, request_id)
                        REFERENCES conversation_generation_runs(conversation_id, request_id)
                        ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_tool_executions_owner_request
                    ON conversation_tool_executions(owner_id, conversation_id, request_id, started_at);

                CREATE TABLE IF NOT EXISTS conversation_tool_reviews (
                    review_id TEXT PRIMARY KEY,
                    execution_id TEXT NOT NULL REFERENCES conversation_tool_executions(execution_id) ON DELETE CASCADE,
                    conversation_id TEXT NOT NULL REFERENCES conversations(conversation_id) ON DELETE CASCADE,
                    owner_id TEXT NOT NULL,
                    request_id TEXT NOT NULL,
                    call_id TEXT NOT NULL,
                    action TEXT NOT NULL CHECK(action IN ('acknowledge_uncertain', 'confirm_provider_completed')),
                    reason TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_tool_reviews_owner_execution
                    ON conversation_tool_reviews(owner_id, conversation_id, request_id, call_id, created_at);
                CREATE TABLE IF NOT EXISTS conversation_provider_reconciliation_leases (
                    execution_id TEXT PRIMARY KEY REFERENCES conversation_tool_executions(execution_id) ON DELETE CASCADE,
                    owner_id TEXT NOT NULL,
                    lease_token TEXT NOT NULL,
                    expires_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_provider_reconciliation_lease_expiry
                    ON conversation_provider_reconciliation_leases(expires_at);
                CREATE TABLE IF NOT EXISTS conversation_provider_outcome_evidence (
                    evidence_id TEXT PRIMARY KEY,
                    execution_id TEXT NOT NULL REFERENCES conversation_tool_executions(execution_id) ON DELETE CASCADE,
                    owner_id TEXT NOT NULL,
                    conversation_id TEXT NOT NULL,
                    request_id TEXT NOT NULL,
                    call_id TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    outcome TEXT NOT NULL CHECK(outcome IN ('confirmed_completed', 'confirmed_not_applied', 'unknown')),
                    idempotency_key TEXT NOT NULL,
                    arguments_sha256 TEXT NOT NULL,
                    evidence_sha256 TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    provider_reference TEXT,
                    recorded_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_provider_evidence_owner_execution
                    ON conversation_provider_outcome_evidence(owner_id, conversation_id, request_id, call_id, recorded_at);
            """)
            tool_columns = {
                row["name"] for row in db.execute(
                    "PRAGMA table_info(conversation_tool_executions)"
                ).fetchall()
            }
            if "idempotency_key" not in tool_columns:
                db.execute("ALTER TABLE conversation_tool_executions ADD COLUMN idempotency_key TEXT NOT NULL DEFAULT ''")
            if "provider_idempotency" not in tool_columns:
                db.execute("ALTER TABLE conversation_tool_executions ADD COLUMN provider_idempotency INTEGER NOT NULL DEFAULT 0")
            review_schema = db.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='conversation_tool_reviews'"
            ).fetchone()
            if review_schema and "'confirm_provider_completed'" not in (review_schema["sql"] or ""):
                db.execute("BEGIN IMMEDIATE")
                db.execute("ALTER TABLE conversation_tool_reviews RENAME TO conversation_tool_reviews_legacy")
                db.execute("""CREATE TABLE conversation_tool_reviews (
                    review_id TEXT PRIMARY KEY,
                    execution_id TEXT NOT NULL REFERENCES conversation_tool_executions(execution_id) ON DELETE CASCADE,
                    conversation_id TEXT NOT NULL REFERENCES conversations(conversation_id) ON DELETE CASCADE,
                    owner_id TEXT NOT NULL,
                    request_id TEXT NOT NULL,
                    call_id TEXT NOT NULL,
                    action TEXT NOT NULL CHECK(action IN ('acknowledge_uncertain', 'confirm_provider_completed')),
                    reason TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )""")
                db.execute("""INSERT INTO conversation_tool_reviews
                    (review_id, execution_id, conversation_id, owner_id, request_id,
                     call_id, action, reason, created_at)
                    SELECT review_id, execution_id, conversation_id, owner_id, request_id,
                           call_id, action, reason, created_at
                    FROM conversation_tool_reviews_legacy""")
                db.execute("DROP TABLE conversation_tool_reviews_legacy")
                db.execute("CREATE INDEX idx_tool_reviews_owner_execution ON conversation_tool_reviews(owner_id, conversation_id, request_id, call_id, created_at)")
                db.commit()

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
        generation_request_id: str | None = None, lease_token: str | None = None,
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
        if lease_token is not None and (role != "assistant" or not isinstance(lease_token, str) or not lease_token.strip() or len(lease_token) > 200):
            raise ValueError("lease_token must be non-empty text for fenced assistant messages.")
        if expected_version is not None and (type(expected_version) is not int or expected_version < 0):
            raise ValueError("expected_version must be a non-negative integer.")
        now, lease_now, message_id = self._now(), time.time(), str(uuid.uuid4())
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
                if lease_token is not None:
                    active_lease = db.execute(
                        """SELECT 1 FROM conversation_generation_leases
                           WHERE conversation_id=? AND owner_id=? AND lease_token=? AND expires_at>?""",
                        (conversation_id, owner, lease_token.strip(), lease_now),
                    ).fetchone()
                    if active_lease is None:
                        raise ConversationConflict("generation lease expired or is no longer owned by this worker.")
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

    def renew_generation_lease(
        self, *, owner_id: str, conversation_id: str, lease_token: str,
        ttl_seconds: int = 600,
    ) -> dict[str, Any]:
        """Extend a live lease only when its owner and fencing token still match."""
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
                cursor = db.execute(
                    """UPDATE conversation_generation_leases
                       SET expires_at=?
                       WHERE conversation_id=? AND owner_id=? AND lease_token=? AND expires_at>?""",
                    (expires_at, conversation_id, owner, lease_token.strip(), now),
                )
                if cursor.rowcount != 1:
                    raise ConversationConflict("generation lease expired or is no longer owned by this worker.")
                db.commit()
            except Exception:
                db.rollback()
                raise
        return {"conversation_id": conversation_id, "expires_at": expires_at}

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



    def begin_tool_execution(
        self, *, owner_id: str, conversation_id: str, request_id: str,
        call_id: str, tool_name: str, arguments_sha256: str, lease_token: str,
        idempotency_key: str | None = None, provider_idempotency: bool = False,
    ) -> dict[str, Any]:
        """Durably claim a tool call before its external side effect can begin."""
        owner = self._owner(owner_id)
        for value, label, maximum in (
            (request_id, "request_id", 160), (call_id, "call_id", 64),
            (tool_name, "tool_name", 128), (lease_token, "lease_token", 200),
        ):
            if not isinstance(value, str) or not value.strip() or len(value) > maximum:
                raise ValueError(f"{label} must be non-empty bounded text.")
        if idempotency_key is None:
            scope = json.dumps(
                ["yuki-tool-v1", owner, conversation_id, request_id.strip(), call_id.strip()],
                ensure_ascii=False, separators=(",", ":"),
            )
            idempotency_key = hashlib.sha256(scope.encode("utf-8")).hexdigest()
        if not isinstance(idempotency_key, str) or len(idempotency_key) != 64 or any(char not in "0123456789abcdef" for char in idempotency_key):
            raise ValueError("idempotency_key must be a lowercase SHA-256 hex digest.")
        if type(provider_idempotency) is not bool:
            raise ValueError("provider_idempotency must be a boolean.")
        if (not isinstance(arguments_sha256, str) or len(arguments_sha256) != 64
                or any(char not in "0123456789abcdef" for char in arguments_sha256)):
            raise ValueError("arguments_sha256 must be a lowercase SHA-256 hex digest.")
        now = time.time()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                lease = db.execute(
                    """SELECT 1 FROM conversation_generation_leases
                       WHERE conversation_id=? AND owner_id=? AND lease_token=? AND expires_at>?""",
                    (conversation_id, owner, lease_token.strip(), now),
                ).fetchone()
                if lease is None:
                    raise ConversationConflict("tool execution requires the active generation lease.")
                generation = db.execute(
                    """SELECT 1 FROM conversation_generation_runs
                       WHERE conversation_id=? AND owner_id=? AND request_id=? AND status='running'""",
                    (conversation_id, owner, request_id.strip()),
                ).fetchone()
                if generation is None:
                    raise ConversationConflict("tool execution generation is not active.")
                previous = db.execute(
                    """SELECT * FROM conversation_tool_executions
                       WHERE owner_id=? AND conversation_id=? AND request_id=? AND call_id=?""",
                    (owner, conversation_id, request_id.strip(), call_id.strip()),
                ).fetchone()
                if previous is not None:
                    if (previous["tool_name"] != tool_name
                            or previous["arguments_sha256"] != arguments_sha256
                            or previous["idempotency_key"] != idempotency_key):
                        raise ConversationConflict("tool call ID was reused with different arguments.")
                    db.commit()
                    return {"created": False, "status": previous["status"],
                            "execution_id": previous["execution_id"],
                            "idempotency_key": previous["idempotency_key"],
                            "provider_idempotency": bool(previous["provider_idempotency"])}
                execution_id = str(uuid.uuid4())
                db.execute(
                    """INSERT INTO conversation_tool_executions
                       (execution_id, conversation_id, owner_id, request_id, call_id,
                        tool_name, arguments_sha256, idempotency_key, provider_idempotency,
                        lease_token, status, started_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'started', ?, ?)""",
                    (execution_id, conversation_id, owner, request_id.strip(), call_id.strip(),
                     tool_name, arguments_sha256, idempotency_key, int(provider_idempotency),
                     lease_token.strip(), self._now(), self._now()),
                )
                db.commit()
                return {"created": True, "status": "started", "execution_id": execution_id,
                        "idempotency_key": idempotency_key,
                        "provider_idempotency": provider_idempotency}
            except Exception:
                db.rollback()
                raise

    def finish_tool_execution(
        self, *, owner_id: str, conversation_id: str, request_id: str,
        call_id: str, lease_token: str, status: str,
    ) -> bool:
        """Finalize a claimed tool call without allowing a stale worker to overwrite it."""
        owner = self._owner(owner_id)
        if status not in {"completed", "failed", "uncertain"}:
            raise ValueError("invalid tool execution status.")
        now = self._now()
        with self._connect() as db:
            cursor = db.execute(
                """UPDATE conversation_tool_executions SET status=?, updated_at=?
                   WHERE owner_id=? AND conversation_id=? AND request_id=? AND call_id=?
                     AND lease_token=? AND status='started'
                     AND EXISTS (
                       SELECT 1 FROM conversation_generation_leases
                       WHERE conversation_id=? AND owner_id=? AND lease_token=? AND expires_at>?
                     )""",
                (status, now, owner, conversation_id, request_id, call_id,
                 lease_token, conversation_id, owner, lease_token, time.time()),
            )
            return cursor.rowcount == 1

    def list_tool_executions(
        self, *, owner_id: str, conversation_id: str, request_id: str,
    ) -> list[dict[str, Any]]:
        owner = self._owner(owner_id)
        with self._connect() as db:
            rows = db.execute(
                """SELECT execution_id, call_id, tool_name, arguments_sha256, idempotency_key,
                          provider_idempotency, status, started_at, updated_at
                   FROM conversation_tool_executions
                   WHERE owner_id=? AND conversation_id=? AND request_id=?
                   ORDER BY started_at, call_id""",
                (owner, conversation_id, request_id),
            ).fetchall()
        return [{**dict(row), "provider_idempotency": bool(row["provider_idempotency"])} for row in rows]

    def acquire_provider_reconciliation_lease(
        self, *, owner_id: str, conversation_id: str, request_id: str,
        call_id: str, lease_token: str, ttl_seconds: int = 120,
    ) -> dict[str, Any]:
        """Claim one unresolved execution for provider lookup across processes."""
        owner = self._owner(owner_id)
        for value, label, maximum in (
            (conversation_id, "conversation_id", 64),
            (request_id, "request_id", 160),
            (call_id, "call_id", 64),
        ):
            if not isinstance(value, str) or not value.strip() or len(value) > maximum:
                raise ValueError(f"{label} must be non-empty bounded text.")
        if not isinstance(lease_token, str) or not lease_token.strip() or len(lease_token) > 200:
            raise ValueError("lease_token must be non-empty text of at most 200 characters.")
        if type(ttl_seconds) is not int or not 5 <= ttl_seconds <= 300:
            raise ValueError("ttl_seconds must be an integer from 5 to 300.")
        now = time.time()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                execution = db.execute(
                    """SELECT execution_id, status FROM conversation_tool_executions
                       WHERE owner_id=? AND conversation_id=? AND request_id=? AND call_id=?""",
                    (owner, conversation_id, request_id.strip(), call_id.strip()),
                ).fetchone()
                if execution is None:
                    raise ConversationNotFound("tool execution not found.")
                if execution["status"] not in {"started", "uncertain"}:
                    raise ConversationConflict("only unresolved executions can be reconciled.")
                current = db.execute(
                    """SELECT lease_token, expires_at FROM conversation_provider_reconciliation_leases
                       WHERE execution_id=? AND owner_id=?""",
                    (execution["execution_id"], owner),
                ).fetchone()
                if current is not None and current["expires_at"] > now:
                    raise ConversationConflict("provider reconciliation is already in progress.")
                db.execute(
                    """INSERT INTO conversation_provider_reconciliation_leases
                       (execution_id, owner_id, lease_token, expires_at) VALUES (?, ?, ?, ?)
                       ON CONFLICT(execution_id) DO UPDATE SET
                         owner_id=excluded.owner_id, lease_token=excluded.lease_token,
                         expires_at=excluded.expires_at""",
                    (execution["execution_id"], owner, lease_token.strip(), now + ttl_seconds),
                )
                db.commit()
                return {"execution_id": execution["execution_id"], "expires_at": now + ttl_seconds}
            except Exception:
                db.rollback()
                raise

    def renew_provider_reconciliation_lease(
        self, *, owner_id: str, execution_id: str, lease_token: str,
        ttl_seconds: int = 120,
    ) -> dict[str, Any]:
        """Extend only a live claim held by this exact worker token."""
        owner = self._owner(owner_id)
        if not isinstance(execution_id, str) or not execution_id.strip():
            raise ValueError("execution_id must be non-empty text.")
        if not isinstance(lease_token, str) or not lease_token.strip():
            raise ValueError("lease_token must be non-empty text.")
        if type(ttl_seconds) is not int or not 5 <= ttl_seconds <= 300:
            raise ValueError("ttl_seconds must be an integer from 5 to 300.")
        now = time.time()
        expires_at = now + ttl_seconds
        with self._connect() as db:
            cursor = db.execute(
                """UPDATE conversation_provider_reconciliation_leases
                   SET expires_at=?
                   WHERE execution_id=? AND owner_id=? AND lease_token=? AND expires_at>?""",
                (expires_at, execution_id.strip(), owner, lease_token.strip(), now),
            )
            if cursor.rowcount != 1:
                raise ConversationConflict("provider reconciliation claim expired or is no longer owned by this worker.")
        return {"execution_id": execution_id.strip(), "expires_at": expires_at}

    def release_provider_reconciliation_lease(
        self, *, owner_id: str, execution_id: str, lease_token: str,
    ) -> bool:
        """Release only the exact claim; stale workers cannot release replacements."""
        owner = self._owner(owner_id)
        if not isinstance(execution_id, str) or not execution_id.strip():
            raise ValueError("execution_id must be non-empty text.")
        if not isinstance(lease_token, str) or not lease_token.strip():
            raise ValueError("lease_token must be non-empty text.")
        with self._connect() as db:
            cursor = db.execute(
                """DELETE FROM conversation_provider_reconciliation_leases
                   WHERE execution_id=? AND owner_id=? AND lease_token=?""",
                (execution_id, owner, lease_token.strip()),
            )
            return cursor.rowcount == 1

    def record_provider_outcome_evidence(
        self, *, owner_id: str, conversation_id: str, request_id: str,
        call_id: str, evidence, reconciliation_lease_token: str,
    ) -> dict[str, Any]:
        """Append typed adapter evidence without mutating execution or authorizing replay."""
        from core.provider_outcome import ProviderOutcomeEvidence
        owner = self._owner(owner_id)
        if not isinstance(evidence, ProviderOutcomeEvidence):
            raise TypeError("evidence must be a validated ProviderOutcomeEvidence.")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                execution = db.execute(
                    """SELECT execution_id, status, idempotency_key, arguments_sha256
                       FROM conversation_tool_executions
                       WHERE owner_id=? AND conversation_id=? AND request_id=? AND call_id=?""",
                    (owner, conversation_id, request_id, call_id),
                ).fetchone()
                if execution is None:
                    raise ConversationNotFound("tool execution not found.")
                if execution["status"] not in {"started", "uncertain"}:
                    raise ConversationConflict("provider evidence can only be recorded for unresolved executions.")
                if (evidence.idempotency_key != execution["idempotency_key"]
                        or evidence.arguments_sha256 != execution["arguments_sha256"]):
                    raise ConversationConflict("provider evidence does not match the stored execution fingerprint.")
                if not isinstance(reconciliation_lease_token, str) or not reconciliation_lease_token.strip():
                    raise ValueError("reconciliation_lease_token must be non-empty text.")
                active_claim = db.execute(
                    """SELECT 1 FROM conversation_provider_reconciliation_leases
                       WHERE execution_id=? AND owner_id=? AND lease_token=? AND expires_at>?""",
                    (execution["execution_id"], owner, reconciliation_lease_token.strip(), time.time()),
                ).fetchone()
                if active_claim is None:
                    raise ConversationConflict("provider reconciliation claim expired or is no longer owned by this worker.")
                # SQLite's IMMEDIATE transaction serializes this check across
                # processes. Repeated delivery of identical provider evidence
                # returns the original ledger entry instead of duplicating it.
                previous = db.execute(
                    """SELECT evidence_id, execution_id, conversation_id, request_id,
                              call_id, provider, outcome, evidence_sha256, observed_at,
                              provider_reference, recorded_at
                       FROM conversation_provider_outcome_evidence
                       WHERE execution_id=? AND evidence_sha256=?""",
                    (execution["execution_id"], evidence.evidence_sha256),
                ).fetchone()
                if previous is not None:
                    if (previous["provider"] != evidence.provider
                            or previous["outcome"] != evidence.outcome.value
                            or previous["observed_at"] != evidence.observed_at
                            or previous["provider_reference"] != evidence.provider_reference):
                        raise ConversationConflict("provider evidence digest was reused with different metadata.")
                    db.commit()
                    return dict(previous)
                evidence_id, recorded_at = str(uuid.uuid4()), self._now()
                db.execute(
                    """INSERT INTO conversation_provider_outcome_evidence
                       (evidence_id, execution_id, owner_id, conversation_id, request_id,
                        call_id, provider, outcome, idempotency_key, arguments_sha256,
                        evidence_sha256, observed_at, provider_reference, recorded_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (evidence_id, execution["execution_id"], owner, conversation_id,
                     request_id, call_id, evidence.provider, evidence.outcome.value,
                     evidence.idempotency_key, evidence.arguments_sha256,
                     evidence.evidence_sha256, evidence.observed_at,
                     evidence.provider_reference, recorded_at),
                )
                db.commit()
                return {
                    "evidence_id": evidence_id, "execution_id": execution["execution_id"],
                    "conversation_id": conversation_id, "request_id": request_id,
                    "call_id": call_id, "provider": evidence.provider,
                    "outcome": evidence.outcome.value, "evidence_sha256": evidence.evidence_sha256,
                    "observed_at": evidence.observed_at,
                    "recorded_at": recorded_at,
                }
            except Exception:
                db.rollback()
                raise

    def list_provider_outcome_evidence(
        self, *, owner_id: str, conversation_id: str, request_id: str, call_id: str,
    ) -> list[dict[str, Any]]:
        owner = self._owner(owner_id)
        with self._connect() as db:
            rows = db.execute(
                """SELECT evidence_id, execution_id, conversation_id, request_id, call_id,
                          provider, outcome, evidence_sha256, observed_at, recorded_at
                   FROM conversation_provider_outcome_evidence
                   WHERE owner_id=? AND conversation_id=? AND request_id=? AND call_id=?
                   ORDER BY recorded_at, evidence_id""",
                (owner, conversation_id, request_id, call_id),
            ).fetchall()
        return [dict(row) for row in rows]

    def review_tool_execution(
        self, *, owner_id: str, conversation_id: str, request_id: str,
        call_id: str, action: str, reason: str,
    ) -> dict[str, Any]:
        """Append an owner review and atomically resolve verified provider completion."""
        owner = self._owner(owner_id)
        for value, label, maximum in (
            (conversation_id, "conversation_id", 64),
            (request_id, "request_id", 160),
            (call_id, "call_id", 64),
        ):
            if not isinstance(value, str) or not value.strip() or len(value) > maximum:
                raise ValueError(f"{label} must be non-empty bounded text.")
        if not isinstance(action, str) or action not in {
            "acknowledge_uncertain", "confirm_provider_completed"
        }:
            raise ConversationConflict("unsupported tool review action.")
        if not isinstance(reason, str) or not reason.strip() or len(reason.strip()) > 1000:
            raise ValueError("reason must be non-empty text of at most 1000 characters.")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                execution = db.execute(
                    """SELECT execution_id, status, provider_idempotency FROM conversation_tool_executions
                       WHERE owner_id=? AND conversation_id=? AND request_id=? AND call_id=?""",
                    (owner, conversation_id, request_id.strip(), call_id.strip()),
                ).fetchone()
                if execution is None:
                    raise ConversationNotFound("tool execution not found.")
                if execution["status"] != "uncertain":
                    raise ConversationConflict("only uncertain tool executions can be reviewed.")
                if action == "confirm_provider_completed":
                    if not execution["provider_idempotency"]:
                        raise ConversationConflict("provider completion requires an idempotency-capable execution.")
                    evidence = db.execute(
                        "SELECT outcome FROM conversation_provider_outcome_evidence "
                        "WHERE execution_id=? ORDER BY recorded_at DESC, evidence_id DESC LIMIT 1",
                        (execution["execution_id"],),
                    ).fetchone()
                    if evidence is None or evidence["outcome"] != "confirmed_completed":
                        raise ConversationConflict("provider completion evidence is not confirmed.")
                    cursor = db.execute(
                        "UPDATE conversation_tool_executions SET status='completed', updated_at=? "
                        "WHERE execution_id=? AND owner_id=? AND status='uncertain'",
                        (self._now(), execution["execution_id"], owner),
                    )
                    if cursor.rowcount != 1:
                        raise ConversationConflict("tool execution changed during review.")
                review_id, created_at = str(uuid.uuid4()), self._now()
                db.execute(
                    """INSERT INTO conversation_tool_reviews
                       (review_id, execution_id, conversation_id, owner_id, request_id,
                        call_id, action, reason, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (review_id, execution["execution_id"], conversation_id, owner,
                     request_id.strip(), call_id.strip(), action, reason.strip(), created_at),
                )
                db.commit()
                return {
                    "review_id": review_id, "execution_id": execution["execution_id"],
                    "conversation_id": conversation_id, "request_id": request_id.strip(),
                    "call_id": call_id.strip(), "action": action,
                    "reason": reason.strip(), "created_at": created_at,
                    "execution_status": ("completed" if action == "confirm_provider_completed" else "uncertain"),
                }
            except Exception:
                db.rollback()
                raise

    def list_tool_execution_reviews(
        self, *, owner_id: str, conversation_id: str, request_id: str, call_id: str,
    ) -> list[dict[str, Any]]:
        owner = self._owner(owner_id)
        with self._connect() as db:
            rows = db.execute(
                """SELECT review_id, execution_id, conversation_id, request_id, call_id,
                          action, reason, created_at
                   FROM conversation_tool_reviews
                   WHERE owner_id=? AND conversation_id=? AND request_id=? AND call_id=?
                   ORDER BY created_at, review_id""",
                (owner, conversation_id, request_id, call_id),
            ).fetchall()
        return [dict(row) for row in rows]

    def mark_started_tool_executions_uncertain(
        self, *, owner_id: str, conversation_id: str, request_id: str,
        lease_token: str,
    ) -> int:
        """Mark in-flight calls uncertain only while the recovery worker owns the lease."""
        owner = self._owner(owner_id)
        if not isinstance(lease_token, str) or not lease_token.strip() or len(lease_token) > 200:
            raise ValueError("lease_token must be non-empty bounded text.")
        with self._connect() as db:
            cursor = db.execute(
                """UPDATE conversation_tool_executions SET status='uncertain', updated_at=?
                   WHERE owner_id=? AND conversation_id=? AND request_id=? AND status='started'
                     AND EXISTS (
                       SELECT 1 FROM conversation_generation_leases
                       WHERE conversation_id=? AND owner_id=? AND lease_token=? AND expires_at>?
                     )""",
                (self._now(), owner, conversation_id, request_id,
                 conversation_id, owner, lease_token.strip(), time.time()),
            )
            return cursor.rowcount

    def update_generation_checkpoint(
        self, *, owner_id: str, conversation_id: str, request_id: str,
        checkpoint: dict[str, Any], lease_token: str,
    ) -> bool:
        """Persist a bounded checkpoint only while this worker owns the live generation lease."""
        owner = self._owner(owner_id)
        if not isinstance(request_id, str) or not request_id.strip() or len(request_id) > 160:
            raise ValueError("request_id must be non-empty text of at most 160 characters.")
        if not isinstance(lease_token, str) or not lease_token.strip() or len(lease_token) > 200:
            raise ValueError("lease_token must be non-empty bounded text.")
        if not isinstance(checkpoint, dict) or len(checkpoint) > 16:
            raise ValueError("checkpoint must be a small object.")
        import json
        encoded = json.dumps(checkpoint, ensure_ascii=True, separators=(",", ":"))
        if len(encoded) > 4096:
            raise ValueError("checkpoint exceeds 4096 characters.")
        with self._connect() as db:
            cursor = db.execute(
                """UPDATE conversation_generation_runs
                   SET checkpoint=?, updated_at=?
                   WHERE owner_id=? AND conversation_id=? AND request_id=? AND status='running'
                     AND EXISTS (
                       SELECT 1 FROM conversation_generation_leases
                       WHERE conversation_id=? AND owner_id=? AND lease_token=? AND expires_at>?
                     )""",
                (encoded, self._now(), owner, conversation_id, request_id.strip(),
                 conversation_id, owner, lease_token.strip(), time.time()),
            )
            return cursor.rowcount == 1

    def finish_generation(
        self, *, owner_id: str, conversation_id: str, request_id: str,
        status: str, lease_token: str,
    ) -> bool:
        """Finalize a generation only while this worker owns the live fencing lease."""
        owner = self._owner(owner_id)
        if status not in {"completed", "interrupted", "failed", "cancelled"}:
            raise ValueError("invalid generation terminal status.")
        if not isinstance(lease_token, str) or not lease_token.strip() or len(lease_token) > 200:
            raise ValueError("lease_token must be non-empty bounded text.")
        now = self._now()
        with self._connect() as db:
            cursor = db.execute(
                """UPDATE conversation_generation_runs
                   SET status=?, updated_at=?, finished_at=?
                   WHERE owner_id=? AND conversation_id=? AND request_id=? AND status='running'
                     AND EXISTS (
                       SELECT 1 FROM conversation_generation_leases
                       WHERE conversation_id=? AND owner_id=? AND lease_token=? AND expires_at>?
                     )""",
                (status, now, now, owner, conversation_id, request_id.strip(),
                 conversation_id, owner, lease_token.strip(), time.time()),
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
