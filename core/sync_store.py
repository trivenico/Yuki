"""SQLite-backed event log for the Yuki sync hub.

Authentication and HTTPS belong to the API boundary. Callers must pass the
authenticated device identity; this store independently checks device status.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sqlite3
import threading
from contextlib import closing
import uuid
from typing import Any

from core.sync_contract import SyncEvent, validate_sync_event


class SyncStoreError(RuntimeError):
    pass


class UnknownDeviceError(SyncStoreError):
    pass


class RevokedDeviceError(SyncStoreError):
    pass


class SyncConflictError(SyncStoreError):
    pass


@dataclass(frozen=True)
class StoredSyncEvent:
    sequence: int
    event: SyncEvent

    def to_dict(self) -> dict[str, Any]:
        return {"sequence": self.sequence, **self.event.to_dict()}


class SyncEventStore:
    """Append-only SQLite event log with idempotency and per-device cursors."""

    def __init__(self, path: str | Path):
        requested_path = str(path)
        self._memory = requested_path == ":memory:"
        self.path = (
            f"file:yuki_sync_{uuid.uuid4().hex}?mode=memory&cache=shared"
            if self._memory else requested_path
        )
        self._lock = threading.RLock()
        self._anchor = None
        if self._memory:
            # Keep one connection alive so per-operation connections share
            # the same in-memory database.
            self._anchor = self._connect()
        else:
            Path(self.path).expanduser().parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self):
        conn = sqlite3.connect(
            self.path, timeout=10, isolation_level=None, uri=self._memory
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=10000")
        if not self._memory:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=FULL")
        return conn

    def close(self) -> None:
        """Release the anchor connection used by a shared in-memory database."""
        if self._anchor is not None:
            self._anchor.close()
            self._anchor = None

    def _initialize(self):
        with self._lock, closing(self._connect()) as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS sync_devices (
                    device_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    revoked_at TEXT
                );
                CREATE TABLE IF NOT EXISTS sync_events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT NOT NULL UNIQUE,
                    user_id TEXT NOT NULL,
                    device_id TEXT NOT NULL,
                    collection TEXT NOT NULL,
                    object_id TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    operation TEXT NOT NULL,
                    occurred_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    FOREIGN KEY(device_id) REFERENCES sync_devices(device_id)
                );
                CREATE INDEX IF NOT EXISTS idx_sync_events_user_sequence
                    ON sync_events(user_id, sequence);
                CREATE INDEX IF NOT EXISTS idx_sync_events_object_revision
                    ON sync_events(user_id, collection, object_id, revision);
                CREATE TABLE IF NOT EXISTS sync_object_heads (
                    user_id TEXT NOT NULL,
                    collection TEXT NOT NULL,
                    object_id TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    sequence INTEGER NOT NULL,
                    PRIMARY KEY(user_id, collection, object_id)
                );
                CREATE TABLE IF NOT EXISTS sync_cursors (
                    device_id TEXT PRIMARY KEY,
                    last_sequence INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(device_id) REFERENCES sync_devices(device_id)
                );
                CREATE TABLE IF NOT EXISTS sync_acknowledgements (
                    device_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    acknowledged_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY(device_id, sequence),
                    FOREIGN KEY(device_id) REFERENCES sync_devices(device_id)
                );
            """)

    def register_device(self, device_id: str, user_id: str) -> None:
        if not isinstance(device_id, str) or not device_id or len(device_id) > 128:
            raise ValueError("Invalid device identifier.")
        if not isinstance(user_id, str) or not user_id or len(user_id) > 128:
            raise ValueError("Invalid user identifier.")
        with self._lock, closing(self._connect()) as conn:
            row = conn.execute("SELECT user_id, active FROM sync_devices WHERE device_id=?", (device_id,)).fetchone()
            if row:
                if row["user_id"] != user_id:
                    raise SyncStoreError("Device identifier is already assigned.")
                if not row["active"]:
                    raise RevokedDeviceError("A revoked device cannot be re-enrolled implicitly.")
                return
            conn.execute("INSERT INTO sync_devices(device_id,user_id) VALUES(?,?)", (device_id, user_id))
            conn.execute("INSERT INTO sync_cursors(device_id,last_sequence) VALUES(?,0)", (device_id,))

    def revoke_device(self, device_id: str, user_id: str) -> None:
        with self._lock, closing(self._connect()) as conn:
            cur = conn.execute(
                "UPDATE sync_devices SET active=0, revoked_at=CURRENT_TIMESTAMP WHERE device_id=? AND user_id=? AND active=1",
                (device_id, user_id),
            )
            if cur.rowcount != 1:
                raise UnknownDeviceError("Active device was not found.")

    def _active_device(self, conn, device_id: str):
        row = conn.execute("SELECT user_id,active FROM sync_devices WHERE device_id=?", (device_id,)).fetchone()
        if row is None:
            raise UnknownDeviceError("Device is not registered.")
        if not row["active"]:
            raise RevokedDeviceError("Device has been revoked.")
        return row["user_id"]

    def append_event(self, authenticated_device_id: str, raw_event: dict[str, Any]) -> StoredSyncEvent:
        event = validate_sync_event(raw_event)
        if event.device_id != authenticated_device_id:
            raise SyncStoreError("Event device does not match authenticated device.")
        import json
        with self._lock, closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                user_id = self._active_device(conn, authenticated_device_id)
                existing = conn.execute(
                    "SELECT sequence,user_id,device_id FROM sync_events WHERE event_id=?",
                    (event.event_id,),
                ).fetchone()
                if existing:
                    if existing["user_id"] != user_id or existing["device_id"] != authenticated_device_id:
                        raise SyncStoreError("Event identifier is already used.")
                    row = conn.execute("SELECT * FROM sync_events WHERE sequence=?", (existing["sequence"],)).fetchone()
                    result = self._row_to_event(row)
                    if result.event.to_dict() != event.to_dict():
                        raise SyncStoreError("Idempotency key was reused with different event content.")
                    conn.commit()
                    return result
                head = conn.execute(
                    "SELECT revision FROM sync_object_heads WHERE user_id=? AND collection=? AND object_id=?",
                    (user_id, event.collection, event.object_id),
                ).fetchone()
                current_revision = head["revision"] if head else 0
                if event.revision != current_revision + 1:
                    raise SyncConflictError(f"Revision conflict: expected {current_revision + 1}, received {event.revision}.")
                cursor = conn.execute(
                    """INSERT INTO sync_events(event_id,user_id,device_id,collection,object_id,revision,
                       operation,occurred_at,payload_json) VALUES(?,?,?,?,?,?,?,?,?)""",
                    (event.event_id,user_id,event.device_id,event.collection,event.object_id,event.revision,
                     event.operation,event.occurred_at,json.dumps(event.payload,ensure_ascii=False,separators=(",",":"))),
                )
                sequence = cursor.lastrowid
                conn.execute(
                    """INSERT INTO sync_object_heads(user_id,collection,object_id,revision,sequence)
                       VALUES(?,?,?,?,?) ON CONFLICT(user_id,collection,object_id)
                       DO UPDATE SET revision=excluded.revision,sequence=excluded.sequence""",
                    (user_id,event.collection,event.object_id,event.revision,sequence),
                )
                conn.commit()
                row = conn.execute("SELECT * FROM sync_events WHERE sequence=?", (sequence,)).fetchone()
                return self._row_to_event(row)
            except Exception:
                conn.rollback()
                raise

    @staticmethod
    def _row_to_event(row) -> StoredSyncEvent:
        import json
        raw = {
            "event_id": row["event_id"], "device_id": row["device_id"],
            "collection": row["collection"], "object_id": row["object_id"],
            "revision": row["revision"], "occurred_at": row["occurred_at"],
            "operation": row["operation"], "payload": json.loads(row["payload_json"]),
        }
        return StoredSyncEvent(row["sequence"], validate_sync_event(raw))

    def pull_events(self, authenticated_device_id: str, after_sequence: int = 0, limit: int = 100) -> list[StoredSyncEvent]:
        if isinstance(after_sequence, bool) or not isinstance(after_sequence, int) or after_sequence < 0:
            raise ValueError("after_sequence must be a non-negative integer.")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500.")
        with self._lock, closing(self._connect()) as conn:
            user_id = self._active_device(conn, authenticated_device_id)
            rows = conn.execute(
                """SELECT * FROM sync_events WHERE user_id=? AND sequence>?
                   ORDER BY sequence ASC LIMIT ?""",
                (user_id, after_sequence, limit),
            ).fetchall()
            return [self._row_to_event(row) for row in rows]

    def acknowledge(self, authenticated_device_id: str, sequence: int) -> int:
        if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 0:
            raise ValueError("sequence must be a non-negative integer.")
        with self._lock, closing(self._connect()) as conn:
            user_id = self._active_device(conn, authenticated_device_id)
            maximum = conn.execute(
                "SELECT COALESCE(MAX(sequence),0) AS maximum FROM sync_events WHERE user_id=?",
                (user_id,),
            ).fetchone()["maximum"]
            if sequence > maximum:
                raise ValueError("Cannot acknowledge an event sequence that has not been issued.")
            current = conn.execute("SELECT last_sequence FROM sync_cursors WHERE device_id=?", (authenticated_device_id,)).fetchone()
            if sequence < current["last_sequence"]:
                raise ValueError("Acknowledgement cursor cannot move backwards.")
            conn.execute(
                "UPDATE sync_cursors SET last_sequence=?,updated_at=CURRENT_TIMESTAMP WHERE device_id=?",
                (sequence, authenticated_device_id),
            )
            if sequence:
                conn.execute(
                    "INSERT OR IGNORE INTO sync_acknowledgements(device_id,sequence) VALUES(?,?)",
                    (authenticated_device_id, sequence),
                )
            return sequence

    def get_cursor(self, authenticated_device_id: str) -> int:
        with self._lock, closing(self._connect()) as conn:
            self._active_device(conn, authenticated_device_id)
            row = conn.execute("SELECT last_sequence FROM sync_cursors WHERE device_id=?", (authenticated_device_id,)).fetchone()
            return row["last_sequence"]

    def health(self) -> dict[str, Any]:
        with self._lock, closing(self._connect()) as conn:
            integrity = conn.execute("PRAGMA quick_check").fetchone()[0]
            events = conn.execute("SELECT COUNT(*) AS count FROM sync_events").fetchone()["count"]
            devices = conn.execute("SELECT COUNT(*) AS count FROM sync_devices WHERE active=1").fetchone()["count"]
            return {"ok": integrity == "ok", "database": "sqlite", "events": events, "active_devices": devices}
