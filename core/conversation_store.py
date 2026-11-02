"""Tenant-scoped, versioned SQLite conversation persistence.

This store persists conversation data only. It does not authenticate callers;
the application must pass the authenticated principal's stable owner_id.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sqlite3
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
        self.database_path = str(Path(database_path).expanduser())
        Path(self.database_path).parent.mkdir(parents=True, exist_ok=True)
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

    def _connect(self):
        db = sqlite3.connect(self.database_path, timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA busy_timeout=10000")
        return db

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
    ) -> dict[str, Any]:
        owner = self._owner(owner_id)
        if not isinstance(role, str) or role not in {"user", "assistant"}:
            raise ValueError("role must be user or assistant.")
        maximum = MAX_USER_CHARS if role == "user" else MAX_ASSISTANT_CHARS
        if not isinstance(content, str) or not content.strip() or len(content) > maximum:
            raise ValueError(f"content must be non-empty text of at most {maximum} characters.")
        if not isinstance(idempotency_key, str) or not idempotency_key.strip() or len(idempotency_key) > 200:
            raise ValueError("idempotency_key must be non-empty text of at most 200 characters.")
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
