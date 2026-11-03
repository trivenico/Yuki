"""Durable, permission-checked agent-to-agent messaging.

Messages are bounded untrusted text envelopes persisted in SQLite. This module
does not execute message content, dispatch tools, or implicitly trust senders.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from typing import Callable, Iterable
from contextlib import contextmanager

_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,99}$")
_MAX_BODY = 8_000
_MAX_THREAD = 120


class AgentMessageError(ValueError):
    """Invalid message envelope or messaging operation."""


class AgentMessageConflict(AgentMessageError):
    """An idempotency key was reused for a different message."""


@dataclass(frozen=True)
class AgentMessage:
    message_id: int
    thread_id: str
    sender_id: str
    recipient_id: str
    message_type: str
    body: str
    idempotency_key: str
    created_at: str


class AgentMessageBus:
    """SQLite-backed inbox/outbox with explicit sender and recipient ACL checks."""

    def __init__(
        self,
        database_path: str,
        *,
        registered_agent_ids: Iterable[str],
        authorize: Callable[[str, str, str], bool],
    ):
        if not isinstance(database_path, str) or not database_path.strip():
            raise ValueError("database_path must be a non-empty path.")
        if not callable(authorize):
            raise TypeError("a trusted authorization callback is required.")
        agents = tuple(registered_agent_ids)
        if not agents or len(agents) > 256 or any(
            not isinstance(agent, str) or not _ID.fullmatch(agent) for agent in agents
        ):
            raise ValueError("registered_agent_ids must contain 1-256 bounded IDs.")
        if len(set(agents)) != len(agents):
            raise ValueError("registered agent IDs must be unique.")
        self.database_path = str(Path(database_path).expanduser())
        Path(self.database_path).parent.mkdir(parents=True, exist_ok=True)
        self.agent_ids = frozenset(agents)
        self.authorize = authorize
        with self._connect() as db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS agent_messages (
                    message_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    thread_id TEXT NOT NULL,
                    sender_id TEXT NOT NULL,
                    recipient_id TEXT NOT NULL,
                    message_type TEXT NOT NULL,
                    body TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    request_digest TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
            """)
            db.execute("CREATE INDEX IF NOT EXISTS idx_agent_messages_inbox ON agent_messages(recipient_id, message_id)")
            db.execute("CREATE INDEX IF NOT EXISTS idx_agent_messages_thread ON agent_messages(thread_id, message_id)")

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.database_path, timeout=5.0)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=5000")
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA synchronous=FULL")
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def _allowed(self, actor_id: str, action: str, resource_id: str) -> bool:
        try:
            return self.authorize(actor_id, action, resource_id) is True
        except Exception:
            return False

    @staticmethod
    def _validate_text(value: str, name: str, limit: int) -> str:
        if not isinstance(value, str) or not value.strip() or len(value) > limit or "\x00" in value:
            raise AgentMessageError(f"{name} must be non-empty bounded text.")
        return value.strip()

    @staticmethod
    def _record(row: sqlite3.Row) -> AgentMessage:
        return AgentMessage(
            message_id=row["message_id"], thread_id=row["thread_id"],
            sender_id=row["sender_id"], recipient_id=row["recipient_id"],
            message_type=row["message_type"], body=row["body"],
            idempotency_key=row["idempotency_key"], created_at=row["created_at"],
        )

    def send(
        self, *, actor_id: str, sender_id: str, recipient_id: str,
        body: str, idempotency_key: str, thread_id: str,
        message_type: str = "finding",
    ) -> AgentMessage:
        actor = self._validate_text(actor_id, "actor_id", 100)
        sender = self._validate_text(sender_id, "sender_id", 100)
        recipient = self._validate_text(recipient_id, "recipient_id", 100)
        thread = self._validate_text(thread_id, "thread_id", _MAX_THREAD)
        text = self._validate_text(body, "body", _MAX_BODY)
        key = self._validate_text(idempotency_key, "idempotency_key", 160)
        kind = self._validate_text(message_type, "message_type", 40)
        if not _ID.fullmatch(sender) or not _ID.fullmatch(recipient) or not _ID.fullmatch(kind):
            raise AgentMessageError("sender, recipient, and message_type must be bounded identifiers.")
        if actor != sender:
            raise PermissionError("agents may only send messages as themselves.")
        if sender not in self.agent_ids or recipient not in self.agent_ids:
            raise AgentMessageError("sender and recipient must be registered agents.")
        if not self._allowed(actor, "agent:message:send", recipient):
            raise PermissionError("agent message send is not authorized.")
        if not self._allowed(recipient, "agent:message:receive", sender):
            raise PermissionError("recipient is not authorized to receive agent messages.")
        payload = {
            "thread_id": thread, "sender_id": sender, "recipient_id": recipient,
            "message_type": kind, "body": text,
        }
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute(
                "SELECT * FROM agent_messages WHERE idempotency_key=?", (key,)
            ).fetchone()
            if existing:
                if existing["request_digest"] != digest:
                    raise AgentMessageConflict("idempotency key was already used for another message.")
                return self._record(existing)
            cur = db.execute("""
                INSERT INTO agent_messages(
                    thread_id, sender_id, recipient_id, message_type, body,
                    idempotency_key, request_digest, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (thread, sender, recipient, kind, text, key, digest, now))
            row = db.execute("SELECT * FROM agent_messages WHERE message_id=?", (cur.lastrowid,)).fetchone()
            return self._record(row)

    def inbox(
        self, *, actor_id: str, after_message_id: int = 0, limit: int = 50,
    ) -> tuple[AgentMessage, ...]:
        actor = self._validate_text(actor_id, "actor_id", 100)
        if actor not in self.agent_ids:
            raise PermissionError("only registered agents may read an inbox.")
        if type(after_message_id) is not int or after_message_id < 0:
            raise ValueError("after_message_id must be a non-negative integer.")
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("limit must be an integer from 1 to 100.")
        if not self._allowed(actor, "agent:message:read", actor):
            raise PermissionError("agent inbox read is not authorized.")
        with self._connect() as db:
            rows = db.execute("""
                SELECT * FROM agent_messages
                WHERE recipient_id=? AND message_id>?
                ORDER BY message_id ASC LIMIT ?
            """, (actor, after_message_id, limit)).fetchall()
        return tuple(self._record(row) for row in rows)

    def thread(
        self, *, actor_id: str, thread_id: str, after_message_id: int = 0,
        limit: int = 50,
    ) -> tuple[AgentMessage, ...]:
        actor = self._validate_text(actor_id, "actor_id", 100)
        thread = self._validate_text(thread_id, "thread_id", _MAX_THREAD)
        if actor not in self.agent_ids:
            raise PermissionError("only registered agents may read a thread.")
        if type(after_message_id) is not int or after_message_id < 0:
            raise ValueError("after_message_id must be a non-negative integer.")
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("limit must be an integer from 1 to 100.")
        if not self._allowed(actor, "agent:message:read", thread):
            raise PermissionError("agent thread read is not authorized.")
        with self._connect() as db:
            rows = db.execute("""
                SELECT * FROM agent_messages
                WHERE thread_id=? AND (sender_id=? OR recipient_id=?) AND message_id>?
                ORDER BY message_id ASC LIMIT ?
            """, (thread, actor, actor, after_message_id, limit)).fetchall()
        return tuple(self._record(row) for row in rows)
