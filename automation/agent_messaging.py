"""Durable, authorization-gated agent-to-agent message mailbox.

Messages are bounded text records. Delivery never executes message content or
grants capabilities; receivers must treat every message as untrusted input.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from typing import Callable

_AGENT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$")
_MAX_BODY = 8_000
_MAX_KEY = 200


@dataclass(frozen=True)
class AgentMessage:
    message_id: int
    idempotency_key: str
    sender_agent_id: str
    recipient_agent_id: str
    body: str
    created_at: str
    acknowledged_at: str | None


class AgentMailbox:
    """SQLite-backed mailbox with per-action authorization and idempotent sends."""

    def __init__(self, database_path: str, *, authorize: Callable[[str, str, str], bool]):
        if not isinstance(database_path, str) or not database_path.strip():
            raise ValueError("database_path must be a non-empty path.")
        if not callable(authorize):
            raise TypeError("a trusted authorization callback is required.")
        self.database_path = str(Path(database_path).expanduser())
        Path(self.database_path).parent.mkdir(parents=True, exist_ok=True)
        self.authorize = authorize
        with self._connect() as db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS agent_messages (
                    message_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    request_digest TEXT NOT NULL,
                    sender_agent_id TEXT NOT NULL,
                    recipient_agent_id TEXT NOT NULL,
                    body TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    acknowledged_at TEXT
                )
            """)
            db.execute("""
                CREATE INDEX IF NOT EXISTS idx_agent_messages_inbox
                ON agent_messages(recipient_agent_id, acknowledged_at, message_id)
            """)

    def _connect(self):
        db = sqlite3.connect(self.database_path, timeout=5.0)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=5000")
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA synchronous=FULL")
        return db

    def _allowed(self, actor_id: str, action: str, resource: str) -> bool:
        if not isinstance(actor_id, str) or not actor_id.strip() or len(actor_id) > 200 or "\x00" in actor_id:
            return False
        try:
            return self.authorize(actor_id.strip(), action, resource) is True
        except Exception:
            return False

    @staticmethod
    def _agent_id(value: str, field: str) -> str:
        if not isinstance(value, str) or not _AGENT_ID.fullmatch(value):
            raise ValueError(f"{field} must be a bounded agent identifier.")
        return value

    @staticmethod
    def _row(row: sqlite3.Row) -> AgentMessage:
        return AgentMessage(
            row["message_id"], row["idempotency_key"], row["sender_agent_id"],
            row["recipient_agent_id"], row["body"], row["created_at"],
            row["acknowledged_at"],
        )

    def send(
        self, *, actor_id: str, sender_agent_id: str, recipient_agent_id: str,
        body: str, idempotency_key: str,
    ) -> AgentMessage:
        sender = self._agent_id(sender_agent_id, "sender_agent_id")
        recipient = self._agent_id(recipient_agent_id, "recipient_agent_id")
        if not isinstance(body, str) or not body.strip() or len(body) > _MAX_BODY or "\x00" in body:
            raise ValueError("message body must be non-empty text of at most 8000 characters.")
        if not isinstance(idempotency_key, str) or not idempotency_key.strip() or len(idempotency_key) > _MAX_KEY:
            raise ValueError("idempotency_key must be non-empty bounded text.")
        if not self._allowed(actor_id, "agent:message:send", sender):
            raise PermissionError("agent message send is not authorized.")
        canonical = json.dumps(
            [sender, recipient, body.strip()], ensure_ascii=False, separators=(",", ":"),
        )
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        with self._connect() as db:
            db.execute(
                """INSERT OR IGNORE INTO agent_messages(
                    idempotency_key, request_digest, sender_agent_id,
                    recipient_agent_id, body, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)""",
                (idempotency_key.strip(), digest, sender, recipient, body.strip(),
                 datetime.now(timezone.utc).isoformat()),
            )
            row = db.execute(
                "SELECT * FROM agent_messages WHERE idempotency_key=?",
                (idempotency_key.strip(),),
            ).fetchone()
            if row is None:
                raise RuntimeError("message persistence failed.")
            if row["request_digest"] != digest:
                raise ValueError("idempotency key was already used for a different message.")
            return self._row(row)

    def inbox(
        self, *, actor_id: str, recipient_agent_id: str, limit: int = 50,
    ) -> list[AgentMessage]:
        recipient = self._agent_id(recipient_agent_id, "recipient_agent_id")
        if not self._allowed(actor_id, "agent:message:read", recipient):
            raise PermissionError("agent inbox read is not authorized.")
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("limit must be an integer from 1 to 100.")
        with self._connect() as db:
            rows = db.execute(
                """SELECT * FROM agent_messages
                   WHERE recipient_agent_id=? AND acknowledged_at IS NULL
                   ORDER BY message_id LIMIT ?""",
                (recipient, limit),
            ).fetchall()
        return [self._row(row) for row in rows]

    def acknowledge(
        self, *, actor_id: str, recipient_agent_id: str, message_id: int,
    ) -> AgentMessage:
        recipient = self._agent_id(recipient_agent_id, "recipient_agent_id")
        if not self._allowed(actor_id, "agent:message:ack", recipient):
            raise PermissionError("agent message acknowledgement is not authorized.")
        if type(message_id) is not int or message_id < 1:
            raise ValueError("message_id must be a positive integer.")
        with self._connect() as db:
            db.execute(
                """UPDATE agent_messages SET acknowledged_at=?
                   WHERE message_id=? AND recipient_agent_id=?
                     AND acknowledged_at IS NULL""",
                (datetime.now(timezone.utc).isoformat(), message_id, recipient),
            )
            row = db.execute(
                "SELECT * FROM agent_messages WHERE message_id=? AND recipient_agent_id=?",
                (message_id, recipient),
            ).fetchone()
            if row is None:
                raise KeyError("message not found in this agent inbox.")
            return self._row(row)
