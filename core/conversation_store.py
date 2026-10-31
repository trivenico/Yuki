"""Durable local conversation history with explicit sync export.

Message content remains local unless the user explicitly queues conversation
sync. This store never performs network I/O.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from pathlib import Path
from typing import Any, Callable

from core.portable_paths import data_root
from core.sync_client import SyncClientError


class ConversationStoreError(RuntimeError):
    pass


class ConversationStore:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path is not None else data_root() / ".yuki" / "conversations.sqlite3"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(self.path, timeout=10)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA foreign_keys=ON")
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=FULL")
        self._db.executescript("""
            CREATE TABLE IF NOT EXISTS conversations (
                conversation_id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                created_at TEXT NOT NULL,
                sync_queued INTEGER NOT NULL DEFAULT 0 CHECK(sync_queued IN (0,1))
            );
            CREATE TABLE IF NOT EXISTS messages (
                message_id TEXT PRIMARY KEY,
                conversation_id TEXT NOT NULL REFERENCES conversations(conversation_id),
                role TEXT NOT NULL CHECK(role IN ('user','assistant','system')),
                content TEXT NOT NULL,
                created_at TEXT NOT NULL,
                ordinal INTEGER NOT NULL,
                sync_queued INTEGER NOT NULL DEFAULT 0 CHECK(sync_queued IN (0,1)),
                UNIQUE(conversation_id, ordinal)
            );
            CREATE TABLE IF NOT EXISTS applied_events (
                event_id TEXT PRIMARY KEY,
                payload_hash TEXT NOT NULL
            );
        """)
        self._db.commit()

    def close(self) -> None:
        self._db.close()

    def create(self, title: str, created_at: str, conversation_id: str | None = None) -> str:
        if not isinstance(title, str) or not title.strip() or len(title) > 160:
            raise ValueError("Conversation title must be 1-160 characters.")
        conversation_id = conversation_id or "conversation_" + uuid.uuid4().hex
        with self._db:
            self._db.execute(
                "INSERT INTO conversations(conversation_id,title,created_at) VALUES(?,?,?)",
                (conversation_id, title.strip(), created_at),
            )
        return conversation_id

    def append(self, conversation_id: str, role: str, content: str, created_at: str,
               message_id: str | None = None) -> str:
        if role not in {"user", "assistant", "system"}:
            raise ValueError("Unsupported conversation message role.")
        if not isinstance(content, str) or len(content) > 64_000:
            raise ValueError("Conversation message exceeds the 64 KB limit.")
        message_id = message_id or "message_" + uuid.uuid4().hex
        row = self._db.execute(
            "SELECT COALESCE(MAX(ordinal),0)+1 AS next_ordinal FROM messages WHERE conversation_id=?",
            (conversation_id,),
        ).fetchone()
        with self._db:
            self._db.execute(
                """INSERT INTO messages(message_id,conversation_id,role,content,created_at,ordinal)
                   VALUES(?,?,?,?,?,?)""",
                (message_id, conversation_id, role, content, created_at, row["next_ordinal"]),
            )
        return message_id

    def list_conversations(self) -> list[dict[str, Any]]:
        rows = self._db.execute(
            """SELECT c.conversation_id,c.title,c.created_at,COUNT(m.message_id) AS message_count
               FROM conversations c LEFT JOIN messages m USING(conversation_id)
               GROUP BY c.conversation_id ORDER BY c.created_at DESC"""
        ).fetchall()
        return [dict(row) for row in rows]

    def messages(self, conversation_id: str) -> list[dict[str, Any]]:
        rows = self._db.execute(
            """SELECT message_id,conversation_id,role,content,created_at,ordinal
               FROM messages WHERE conversation_id=? ORDER BY ordinal""",
            (conversation_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def preview_sync(self) -> dict[str, int]:
        conversations = self._db.execute(
            "SELECT COUNT(*) FROM conversations WHERE sync_queued=0"
        ).fetchone()[0]
        messages = self._db.execute(
            "SELECT COUNT(*) FROM messages WHERE sync_queued=0"
        ).fetchone()[0]
        return {"conversations": conversations, "messages": messages}

    @staticmethod
    def _event(device_id: str, collection: str, object_id: str,
               payload: dict[str, Any], occurred_at: str) -> dict[str, Any]:
        event_id = "event_" + hashlib.sha256(
            (device_id + ":" + collection + ":" + object_id).encode()
        ).hexdigest()[:32]
        return {
            "event_id": event_id, "device_id": device_id, "collection": collection,
            "object_id": object_id, "revision": 1, "occurred_at": occurred_at,
            "operation": "upsert", "payload": payload,
        }

    def queue_sync(self, device_id: str, enqueue: Callable[[dict[str, Any]], bool]) -> dict[str, int]:
        if not isinstance(device_id, str) or len(device_id) < 8:
            raise SyncClientError("A paired device is required before queuing conversations.")
        queued_conversations = queued_messages = 0
        conversations = self._db.execute(
            "SELECT * FROM conversations WHERE sync_queued=0 ORDER BY created_at"
        ).fetchall()
        for row in conversations:
            payload = {"title": row["title"], "created_at": row["created_at"]}
            event = self._event(device_id, "conversation_metadata", row["conversation_id"],
                                payload, row["created_at"])
            enqueue(event)
            with self._db:
                self._db.execute("UPDATE conversations SET sync_queued=1 WHERE conversation_id=?",
                                 (row["conversation_id"],))
            queued_conversations += 1

        messages = self._db.execute(
            """SELECT m.* FROM messages m JOIN conversations c USING(conversation_id)
               WHERE m.sync_queued=0 ORDER BY c.created_at,m.ordinal"""
        ).fetchall()
        for row in messages:
            payload = {
                "conversation_id": row["conversation_id"], "role": row["role"],
                "content": row["content"], "created_at": row["created_at"],
                "ordinal": row["ordinal"],
            }
            event = self._event(device_id, "conversation_messages", row["message_id"],
                                payload, row["created_at"])
            enqueue(event)
            with self._db:
                self._db.execute("UPDATE messages SET sync_queued=1 WHERE message_id=?",
                                 (row["message_id"],))
            queued_messages += 1
        return {"conversations": queued_conversations, "messages": queued_messages}

    def apply_remote(self, event: dict[str, Any]) -> str:
        from core.sync_contract import validate_sync_event
        normalized = validate_sync_event(event).to_dict()
        collection, object_id, payload = (
            normalized["collection"], normalized["object_id"], normalized["payload"]
        )
        if normalized["operation"] != "upsert":
            raise SyncClientError("Conversation deletions are not supported yet.")
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        prior = self._db.execute(
            "SELECT payload_hash FROM applied_events WHERE event_id=?",
            (normalized["event_id"],),
        ).fetchone()
        if prior:
            if prior["payload_hash"] != digest:
                raise SyncClientError("A conversation event ID was reused with different content.")
            return "duplicate"

        if collection == "conversation_metadata":
            if set(payload) != {"title", "created_at"} or not isinstance(payload["title"], str) or len(payload["title"]) > 160:
                raise SyncClientError("Remote conversation metadata is invalid.")
            if not isinstance(payload["created_at"], str):
                raise SyncClientError("Remote conversation timestamp is invalid.")
            existing = self._db.execute(
                "SELECT conversation_id FROM conversations WHERE conversation_id=?", (object_id,)
            ).fetchone()
            if existing:
                raise SyncClientError("Conversation ID already exists without matching sync event history.")
            with self._db:
                self._db.execute(
                    "INSERT INTO conversations(conversation_id,title,created_at,sync_queued) VALUES(?,?,?,1)",
                    (object_id, payload["title"], payload["created_at"]),
                )
                self._db.execute("INSERT INTO applied_events(event_id,payload_hash) VALUES(?,?)",
                                 (normalized["event_id"], digest))
            return "created"

        if collection == "conversation_messages":
            if set(payload) != {"conversation_id", "role", "content", "created_at", "ordinal"}:
                raise SyncClientError("Remote conversation message fields are invalid.")
            if payload["role"] not in {"user", "assistant", "system"}:
                raise SyncClientError("Remote conversation role is invalid.")
            if not isinstance(payload["content"], str) or len(payload["content"]) > 64_000:
                raise SyncClientError("Remote conversation content is invalid.")
            if not isinstance(payload["ordinal"], int) or isinstance(payload["ordinal"], bool) or payload["ordinal"] < 1:
                raise SyncClientError("Remote conversation message order is invalid.")
            if not self._db.execute(
                "SELECT 1 FROM conversations WHERE conversation_id=?", (payload["conversation_id"],)
            ).fetchone():
                raise SyncClientError("Remote message arrived before its conversation metadata.")
            existing = self._db.execute(
                "SELECT message_id FROM messages WHERE message_id=?", (object_id,)
            ).fetchone()
            if existing:
                raise SyncClientError("Message ID already exists without matching sync event history.")
            with self._db:
                self._db.execute(
                    """INSERT INTO messages(message_id,conversation_id,role,content,created_at,ordinal,sync_queued)
                       VALUES(?,?,?,?,?,?,1)""",
                    (object_id, payload["conversation_id"], payload["role"], payload["content"],
                     payload["created_at"], payload["ordinal"]),
                )
                self._db.execute("INSERT INTO applied_events(event_id,payload_hash) VALUES(?,?)",
                                 (normalized["event_id"], digest))
            return "created"
        raise SyncClientError("This device does not accept this sync collection.")
