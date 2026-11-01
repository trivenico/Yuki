"""Opt-in, owner-scoped SQLite persistence for companion conversations.

Conversation content is private application data. This store deliberately has no
implicit global history, cross-user lookup, provider calls, or background sync.
Callers must authenticate and authorize before using it.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sqlite3
import uuid
from contextlib import contextmanager
from typing import Any

_ID = re.compile(r"^[A-Za-z0-9_-]{8,80}$")
_ROLES = frozenset({"user", "assistant"})
_MAX_MESSAGES = 200
_MAX_CONTENT = 8_000
_MAX_TOTAL = 500_000


class ConversationStoreError(RuntimeError):
    """Safe persistence-layer error."""


@dataclass(frozen=True)
class StoredConversation:
    conversation_id: str
    owner_id: str
    title: str
    version: int
    created_at: str
    updated_at: str
    messages: tuple[dict[str, str], ...]


class ConversationStore:
    """SQLite store with owner scoping and optimistic concurrency."""

    def __init__(self, database_path: str | os.PathLike[str]) -> None:
        if not isinstance(database_path, (str, os.PathLike)) or not str(database_path).strip():
            raise ValueError("database_path must be a non-empty path.")
        self.path = Path(database_path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.path.parent, 0o700)
        except OSError:
            pass
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.path), timeout=5.0, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=5000")
        return connection

    @contextmanager
    def _connection(self):
        db = self._connect()
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def _initialize(self) -> None:
        try:
            with self._connection() as db:
                db.execute("""
                    CREATE TABLE IF NOT EXISTS companion_conversations (
                        conversation_id TEXT PRIMARY KEY,
                        owner_id TEXT NOT NULL,
                        title TEXT NOT NULL,
                        version INTEGER NOT NULL DEFAULT 0,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    )
                """)
                db.execute("""
                    CREATE TABLE IF NOT EXISTS companion_messages (
                        conversation_id TEXT NOT NULL,
                        sequence INTEGER NOT NULL,
                        role TEXT NOT NULL CHECK(role IN ('user', 'assistant')),
                        content TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        PRIMARY KEY(conversation_id, sequence),
                        FOREIGN KEY(conversation_id) REFERENCES companion_conversations(conversation_id) ON DELETE CASCADE
                    )
                """)
                db.execute("CREATE INDEX IF NOT EXISTS idx_companion_owner_updated ON companion_conversations(owner_id, updated_at DESC)")
            try:
                os.chmod(self.path, 0o600)
            except OSError:
                pass
        except sqlite3.Error as exc:
            raise ConversationStoreError("Could not initialize the private conversation store.") from exc

    @staticmethod
    def _owner(owner_id: str) -> str:
        if not isinstance(owner_id, str) or not owner_id.strip() or len(owner_id) > 200 or "\x00" in owner_id:
            raise ValueError("owner_id must be bounded non-empty text.")
        return owner_id.strip()

    @staticmethod
    def _id(value: str) -> str:
        if not isinstance(value, str) or not _ID.fullmatch(value):
            raise ValueError("conversation_id is invalid.")
        return value

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def create(self, *, owner_id: str, title: str = "New conversation") -> StoredConversation:
        owner = self._owner(owner_id)
        if not isinstance(title, str) or not title.strip() or len(title.strip()) > 120 or "\x00" in title:
            raise ValueError("title must be 1-120 characters.")
        conversation_id = uuid.uuid4().hex
        now = self._now()
        try:
            with self._connect() as db:
                db.execute(
                    "INSERT INTO companion_conversations(conversation_id,owner_id,title,created_at,updated_at) VALUES(?,?,?,?,?)",
                    (conversation_id, owner, title.strip(), now, now),
                )
            return self.get(owner_id=owner, conversation_id=conversation_id)
        except sqlite3.Error as exc:
            raise ConversationStoreError("Could not create the conversation.") from exc

    def append_turn(
        self, *, owner_id: str, conversation_id: str,
        user_message: str, assistant_message: str, expected_version: int,
    ) -> StoredConversation:
        owner = self._owner(owner_id)
        cid = self._id(conversation_id)
        if type(expected_version) is not int or expected_version < 0:
            raise ValueError("expected_version must be a non-negative integer.")
        pair = (("user", user_message), ("assistant", assistant_message))
        for role, content in pair:
            if not isinstance(content, str) or not content.strip() or len(content) > _MAX_CONTENT or "\x00" in content:
                raise ValueError(f"{role} message must be bounded non-empty text.")
        now = self._now()
        try:
            with self._connect() as db:
                db.execute("BEGIN IMMEDIATE")
                row = db.execute(
                    "SELECT version FROM companion_conversations WHERE conversation_id=? AND owner_id=?",
                    (cid, owner),
                ).fetchone()
                if row is None:
                    raise KeyError("conversation not found.")
                if row["version"] != expected_version:
                    raise ConversationStoreError("Conversation changed; reload it before appending.")
                count = db.execute(
                    "SELECT COUNT(*) AS n, COALESCE(SUM(LENGTH(content)),0) AS chars FROM companion_messages WHERE conversation_id=?",
                    (cid,),
                ).fetchone()
                if count["n"] + 2 > _MAX_MESSAGES or count["chars"] + len(user_message) + len(assistant_message) > _MAX_TOTAL:
                    raise ConversationStoreError("Conversation reached its configured history limit.")
                sequence = count["n"] + 1
                for offset, (role, content) in enumerate(pair):
                    db.execute(
                        "INSERT INTO companion_messages(conversation_id,sequence,role,content,created_at) VALUES(?,?,?,?,?)",
                        (cid, sequence + offset, role, content, now),
                    )
                changed = db.execute(
                    "UPDATE companion_conversations SET version=version+1,updated_at=? WHERE conversation_id=? AND owner_id=? AND version=?",
                    (now, cid, owner, expected_version),
                ).rowcount
                if changed != 1:
                    raise ConversationStoreError("Conversation changed; reload it before appending.")
            return self.get(owner_id=owner, conversation_id=cid)
        except (KeyError, ValueError, ConversationStoreError):
            raise
        except sqlite3.Error as exc:
            raise ConversationStoreError("Could not save the conversation turn.") from exc

    def get(self, *, owner_id: str, conversation_id: str) -> StoredConversation:
        owner = self._owner(owner_id)
        cid = self._id(conversation_id)
        try:
            with self._connect() as db:
                row = db.execute(
                    "SELECT * FROM companion_conversations WHERE conversation_id=? AND owner_id=?",
                    (cid, owner),
                ).fetchone()
                if row is None:
                    raise KeyError("conversation not found.")
                messages = db.execute(
                    "SELECT role,content FROM companion_messages WHERE conversation_id=? ORDER BY sequence",
                    (cid,),
                ).fetchall()
            return StoredConversation(
                row["conversation_id"], row["owner_id"], row["title"], row["version"],
                row["created_at"], row["updated_at"],
                tuple({"role": item["role"], "content": item["content"]} for item in messages),
            )
        except KeyError:
            raise
        except sqlite3.Error as exc:
            raise ConversationStoreError("Could not read the conversation.") from exc

    def list(self, *, owner_id: str, limit: int = 50) -> list[dict[str, Any]]:
        owner = self._owner(owner_id)
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100.")
        try:
            with self._connect() as db:
                rows = db.execute(
                    "SELECT conversation_id,title,version,created_at,updated_at FROM companion_conversations WHERE owner_id=? ORDER BY updated_at DESC LIMIT ?",
                    (owner, limit),
                ).fetchall()
            return [dict(row) for row in rows]
        except sqlite3.Error as exc:
            raise ConversationStoreError("Could not list conversations.") from exc

    def delete(self, *, owner_id: str, conversation_id: str) -> bool:
        owner = self._owner(owner_id)
        cid = self._id(conversation_id)
        try:
            with self._connect() as db:
                changed = db.execute(
                    "DELETE FROM companion_conversations WHERE conversation_id=? AND owner_id=?",
                    (cid, owner),
                ).rowcount
            return changed == 1
        except sqlite3.Error as exc:
            raise ConversationStoreError("Could not delete the conversation.") from exc
