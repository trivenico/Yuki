"""Workspace-scoped persistent conversation sessions for Yuki.

The store persists conversation data only. It does not execute tools, grant approvals,
or infer authorization from a session/message record.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Any
from uuid import uuid4

SESSION_STATUSES = frozenset({"active", "archived"})
MESSAGE_ROLES = frozenset({"system", "user", "assistant", "tool"})
_MAX_WORKSPACE_ID = 128
_MAX_TITLE = 200
_MAX_CONTENT = 64_000
_MAX_METADATA = 8_000


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_object(value: Any, *, label: str, maximum: int) -> str:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object.")
    try:
        rendered = json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must contain finite JSON-compatible values.") from exc
    if len(rendered.encode("utf-8")) > maximum:
        raise ValueError(f"{label} exceeds {maximum} bytes.")
    return rendered


class ConversationStore:
    """SQLite-backed conversations with mandatory workspace scoping and idempotent writes."""

    def __init__(self, database: str | Path, *, busy_timeout_ms: int = 5000) -> None:
        raw = str(database)
        if not raw.strip() or raw == ":memory:":
            raise ValueError("ConversationStore requires a persistent database path.")
        if isinstance(busy_timeout_ms, bool) or not isinstance(busy_timeout_ms, int) or not 1 <= busy_timeout_ms <= 120000:
            raise ValueError("busy_timeout_ms must be between 1 and 120000.")
        self.database = str(Path(raw).expanduser().resolve())
        self.busy_timeout_ms = busy_timeout_ms
        self._lock = RLock()
        Path(self.database).parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.database, timeout=self.busy_timeout_ms / 1000)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        db.execute(f"PRAGMA busy_timeout = {self.busy_timeout_ms}")
        return db

    def _initialize(self) -> None:
        with closing(self._connect()) as db:
            with db:
                db.execute("PRAGMA journal_mode = DELETE")
                db.executescript("""
                    CREATE TABLE IF NOT EXISTS conversations (
                        id TEXT PRIMARY KEY,
                        workspace_id TEXT NOT NULL,
                        title TEXT NOT NULL,
                        status TEXT NOT NULL CHECK(status IN ('active','archived')),
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        metadata_json TEXT NOT NULL DEFAULT '{}'
                    );
                    CREATE INDEX IF NOT EXISTS conversations_workspace_updated
                        ON conversations(workspace_id, status, updated_at DESC);
                    CREATE TABLE IF NOT EXISTS conversation_messages (
                        sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                        id TEXT NOT NULL UNIQUE,
                        conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                        workspace_id TEXT NOT NULL,
                        role TEXT NOT NULL CHECK(role IN ('system','user','assistant','tool')),
                        content TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        idempotency_key TEXT,
                        metadata_json TEXT NOT NULL DEFAULT '{}',
                        UNIQUE(conversation_id, idempotency_key)
                    );
                    CREATE INDEX IF NOT EXISTS conversation_messages_order
                        ON conversation_messages(conversation_id, sequence);
                """)

    @staticmethod
    def _workspace(workspace_id: str) -> str:
        if not isinstance(workspace_id, str) or not workspace_id.strip() or len(workspace_id) > _MAX_WORKSPACE_ID:
            raise ValueError("workspace_id must be non-empty text of at most 128 characters.")
        return workspace_id.strip()

    @staticmethod
    def _session(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["metadata"] = json.loads(item.pop("metadata_json"))
        return item

    @staticmethod
    def _message(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["metadata"] = json.loads(item.pop("metadata_json"))
        return item

    def create_session(self, workspace_id: str, title: str = "New conversation", *,
                       metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        workspace = self._workspace(workspace_id)
        if not isinstance(title, str) or not title.strip() or len(title.strip()) > _MAX_TITLE:
            raise ValueError("Session title must be non-empty text of at most 200 characters.")
        metadata_json = _json_object(metadata if metadata is not None else {}, label="Session metadata", maximum=_MAX_METADATA)
        session_id, now = uuid4().hex, _now()
        with self._lock, closing(self._connect()) as db:
            with db:
                db.execute(
                    "INSERT INTO conversations(id,workspace_id,title,status,created_at,updated_at,metadata_json) VALUES(?,?,?,?,?,?,?)",
                    (session_id, workspace, title.strip(), "active", now, now, metadata_json),
                )
                row = db.execute("SELECT * FROM conversations WHERE id=?", (session_id,)).fetchone()
        return self._session(row)

    def get_session(self, workspace_id: str, session_id: str) -> dict[str, Any]:
        workspace = self._workspace(workspace_id)
        if not isinstance(session_id, str) or not session_id.strip():
            raise ValueError("session_id must be non-empty.")
        with closing(self._connect()) as db:
            row = db.execute(
                "SELECT * FROM conversations WHERE id=? AND workspace_id=?", (session_id, workspace)
            ).fetchone()
        if row is None:
            raise KeyError("Conversation not found in this workspace.")
        return self._session(row)

    def list_sessions(self, workspace_id: str, *, limit: int = 50, include_archived: bool = False) -> list[dict[str, Any]]:
        workspace = self._workspace(workspace_id)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 200:
            raise ValueError("limit must be between 1 and 200.")
        if not isinstance(include_archived, bool):
            raise TypeError("include_archived must be a boolean.")
        query = "SELECT * FROM conversations WHERE workspace_id=?"
        params: list[Any] = [workspace]
        if not include_archived:
            query += " AND status='active'"
        query += " ORDER BY updated_at DESC, id DESC LIMIT ?"
        params.append(limit)
        with closing(self._connect()) as db:
            rows = db.execute(query, params).fetchall()
        return [self._session(row) for row in rows]

    def append_message(self, workspace_id: str, session_id: str, role: str, content: str, *,
                       idempotency_key: str | None = None,
                       metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        workspace = self._workspace(workspace_id)
        if not isinstance(session_id, str) or not session_id.strip():
            raise ValueError("session_id must be non-empty.")
        if not isinstance(role, str) or role not in MESSAGE_ROLES:
            raise ValueError("Unsupported conversation message role.")
        if not isinstance(content, str) or not content.strip() or len(content) > _MAX_CONTENT:
            raise ValueError("Message content must be non-empty text of at most 64000 characters.")
        if idempotency_key is not None and (not isinstance(idempotency_key, str) or not idempotency_key.strip() or len(idempotency_key) > 200):
            raise ValueError("idempotency_key must be non-empty text of at most 200 characters.")
        metadata_json = _json_object(metadata if metadata is not None else {}, label="Message metadata", maximum=_MAX_METADATA)
        message_id, now = uuid4().hex, _now()
        with self._lock, closing(self._connect()) as db:
            with db:
                db.execute("BEGIN IMMEDIATE")
                session = db.execute(
                    "SELECT status FROM conversations WHERE id=? AND workspace_id=?", (session_id, workspace)
                ).fetchone()
                if session is None:
                    raise KeyError("Conversation not found in this workspace.")
                if session["status"] != "active":
                    raise ValueError("Archived conversations are read-only.")
                if idempotency_key is not None:
                    existing = db.execute(
                        "SELECT * FROM conversation_messages WHERE conversation_id=? AND idempotency_key=?",
                        (session_id, idempotency_key),
                    ).fetchone()
                    if existing is not None:
                        item = self._message(existing)
                        if item["workspace_id"] != workspace or item["role"] != role or item["content"] != content or item["metadata"] != json.loads(metadata_json):
                            raise ValueError("Idempotency key already exists with different message content.")
                        return item
                db.execute(
                    "INSERT INTO conversation_messages(id,conversation_id,workspace_id,role,content,created_at,idempotency_key,metadata_json) VALUES(?,?,?,?,?,?,?,?)",
                    (message_id, session_id, workspace, role, content, now, idempotency_key, metadata_json),
                )
                db.execute("UPDATE conversations SET updated_at=? WHERE id=? AND workspace_id=?", (now, session_id, workspace))
                row = db.execute("SELECT * FROM conversation_messages WHERE id=?", (message_id,)).fetchone()
        return self._message(row)

    def list_messages(self, workspace_id: str, session_id: str, *, after_sequence: int = 0, limit: int = 200) -> list[dict[str, Any]]:
        workspace = self._workspace(workspace_id)
        if isinstance(after_sequence, bool) or not isinstance(after_sequence, int) or after_sequence < 0:
            raise ValueError("after_sequence must be a non-negative integer.")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500.")
        self.get_session(workspace, session_id)
        with closing(self._connect()) as db:
            rows = db.execute(
                "SELECT * FROM conversation_messages WHERE conversation_id=? AND workspace_id=? AND sequence>? ORDER BY sequence LIMIT ?",
                (session_id, workspace, after_sequence, limit),
            ).fetchall()
        return [self._message(row) for row in rows]

    def archive_session(self, workspace_id: str, session_id: str) -> dict[str, Any]:
        workspace = self._workspace(workspace_id)
        if not isinstance(session_id, str) or not session_id.strip():
            raise ValueError("session_id must be non-empty.")
        with self._lock, closing(self._connect()) as db:
            with db:
                db.execute("BEGIN IMMEDIATE")
                db.execute(
                    "UPDATE conversations SET status='archived',updated_at=? WHERE id=? AND workspace_id=? AND status='active'",
                    (_now(), session_id, workspace),
                )
                row = db.execute("SELECT * FROM conversations WHERE id=? AND workspace_id=?", (session_id, workspace)).fetchone()
                if row is None:
                    raise KeyError("Conversation not found in this workspace.")
        return self._session(row)
