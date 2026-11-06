"""Durable, paired-device-scoped notification outbox.

This is a delivery record, not an OS notification transport. Events are visible
only to the currently authenticated recipient device and are acknowledged by
that same device. Payloads are bounded JSON data; callers must avoid secrets.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import re
import sqlite3
import uuid
from pathlib import Path
from typing import Any

from core.device_pairing import DevicePairingService


_EVENT_TYPE = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")
_DEDUPE_KEY = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
_MAX_PAYLOAD_BYTES = 8_192
_MAX_PAGE_SIZE = 100
_DEFAULT_TTL_SECONDS = 7 * 24 * 60 * 60


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _stamp(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp_must_be_timezone_aware")
    return value.astimezone(timezone.utc).isoformat()


@dataclass(frozen=True)
class NotificationEvent:
    event_id: str
    sequence: int
    event_type: str
    payload: dict[str, Any]
    created_at: str
    expires_at: str
    acknowledged_at: str | None


@dataclass(frozen=True)
class NotificationPage:
    events: list[NotificationEvent]
    next_cursor: int
    has_more: bool


class DeviceNotificationOutbox:
    """SQLite-backed notification outbox with device-bound access control."""

    def __init__(
        self,
        database_path: str | Path,
        pairing: DevicePairingService,
        *,
        default_ttl_seconds: int = _DEFAULT_TTL_SECONDS,
    ):
        if not isinstance(pairing, DevicePairingService):
            raise TypeError("pairing must be a DevicePairingService")
        if (type(default_ttl_seconds) is not int
                or not 60 <= default_ttl_seconds <= 30 * 24 * 60 * 60):
            raise ValueError("default_ttl_seconds_out_of_range")
        self.database_path = str(Path(database_path).expanduser())
        self.pairing = pairing
        self.default_ttl_seconds = default_ttl_seconds
        Path(self.database_path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("""
                CREATE TABLE IF NOT EXISTS device_notification_outbox (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT NOT NULL UNIQUE,
                    owner_id TEXT NOT NULL,
                    device_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    dedupe_key TEXT,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    acknowledged_at TEXT,
                    UNIQUE(device_id, dedupe_key)
                )
            """)
            db.execute("""
                CREATE INDEX IF NOT EXISTS idx_notification_device_sequence
                ON device_notification_outbox(device_id, sequence)
            """)
            db.execute("""
                CREATE INDEX IF NOT EXISTS idx_notification_expiry
                ON device_notification_outbox(expires_at)
            """)

    def _connect(self):
        return _Connection(sqlite3.connect(self.database_path, timeout=5.0))

    @staticmethod
    def _validate_payload(payload: Any) -> str:
        if not isinstance(payload, dict):
            raise ValueError("invalid_notification_payload")
        try:
            encoded = json.dumps(
                payload, ensure_ascii=False, allow_nan=False,
                separators=(",", ":"), sort_keys=True,
            )
        except (TypeError, ValueError):
            raise ValueError("invalid_notification_payload") from None
        if len(encoded.encode("utf-8")) > _MAX_PAYLOAD_BYTES:
            raise ValueError("notification_payload_too_large")
        return encoded

    def enqueue(
        self,
        *,
        owner_id: str,
        device_id: str,
        event_type: str,
        payload: dict[str, Any],
        dedupe_key: str | None = None,
        now: datetime | None = None,
        ttl_seconds: int | None = None,
    ) -> NotificationEvent:
        """Create an event for an active device; repeat dedupe keys return the original."""
        if not isinstance(owner_id, str) or not 1 <= len(owner_id) <= 128 or not owner_id.strip():
            raise ValueError("invalid_owner")
        if not isinstance(device_id, str) or not 1 <= len(device_id) <= 128:
            raise ValueError("invalid_device")
        if not isinstance(event_type, str) or not _EVENT_TYPE.fullmatch(event_type):
            raise ValueError("invalid_event_type")
        if dedupe_key is not None and (
            not isinstance(dedupe_key, str) or not _DEDUPE_KEY.fullmatch(dedupe_key)
        ):
            raise ValueError("invalid_dedupe_key")
        payload_json = self._validate_payload(payload)
        ttl = self.default_ttl_seconds if ttl_seconds is None else ttl_seconds
        if type(ttl) is not int or not 60 <= ttl <= 30 * 24 * 60 * 60:
            raise ValueError("ttl_seconds_out_of_range")
        instant = now or _now()
        created_at = _stamp(instant)
        expires_at = _stamp(instant + timedelta(seconds=ttl))
        if not self.pairing.owns_active_device(owner_id, device_id):
            raise PermissionError("active_device_required")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if dedupe_key is not None:
                existing = db.execute("""
                    SELECT * FROM device_notification_outbox
                    WHERE owner_id=? AND device_id=? AND dedupe_key=?
                """, (owner_id, device_id, dedupe_key)).fetchone()
                if existing is not None:
                    return self._event(existing)
            event_id = "evt_" + uuid.uuid4().hex
            db.execute("""
                INSERT INTO device_notification_outbox(
                    event_id,owner_id,device_id,event_type,payload_json,
                    dedupe_key,created_at,expires_at
                ) VALUES(?,?,?,?,?,?,?,?)
            """, (event_id, owner_id, device_id, event_type, payload_json,
                  dedupe_key, created_at, expires_at))
            row = db.execute(
                "SELECT * FROM device_notification_outbox WHERE event_id=?",
                (event_id,),
            ).fetchone()
            return self._event(row)

    def poll(
        self,
        token: str,
        *,
        after_sequence: int = 0,
        limit: int = 50,
        now: datetime | None = None,
    ) -> NotificationPage:
        """Return unacknowledged, unexpired events addressed only to this device."""
        if type(after_sequence) is not int or not 0 <= after_sequence <= 2**63 - 1:
            raise ValueError("invalid_cursor")
        if type(limit) is not int or not 1 <= limit <= _MAX_PAGE_SIZE:
            raise ValueError("invalid_page_size")
        device = self.pairing.authenticate_device(token)
        if device is None:
            raise PermissionError("invalid_device_credential")
        instant = _stamp(now or _now())
        with self._connect() as db:
            rows = db.execute("""
                SELECT * FROM device_notification_outbox
                WHERE device_id=? AND sequence>?
                  AND acknowledged_at IS NULL AND expires_at>?
                ORDER BY sequence ASC LIMIT ?
            """, (device.device_id, after_sequence, instant, limit + 1)).fetchall()
        has_more = len(rows) > limit
        visible = rows[:limit]
        next_cursor = visible[-1]["sequence"] if visible else after_sequence
        return NotificationPage(
            events=[self._event(row) for row in visible],
            next_cursor=next_cursor,
            has_more=has_more,
        )

    def acknowledge(
        self,
        token: str,
        event_id: str,
        *,
        now: datetime | None = None,
    ) -> bool:
        """Acknowledge an event only from its active recipient device.

        Repeated acknowledgements by the recipient are idempotent. Unknown,
        expired, and other-device event IDs return False without disclosure.
        """
        if not isinstance(event_id, str) or not re.fullmatch(r"evt_[0-9a-f]{32}", event_id):
            return False
        device = self.pairing.authenticate_device(token)
        if device is None:
            raise PermissionError("invalid_device_credential")
        instant = _stamp(now or _now())
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("""
                SELECT acknowledged_at, expires_at FROM device_notification_outbox
                WHERE event_id=? AND owner_id=? AND device_id=?
            """, (event_id, device.owner_id, device.device_id)).fetchone()
            if row is None or row["expires_at"] <= instant:
                return False
            if row["acknowledged_at"] is None:
                db.execute("""
                    UPDATE device_notification_outbox SET acknowledged_at=?
                    WHERE event_id=? AND owner_id=? AND device_id=?
                      AND acknowledged_at IS NULL
                """, (instant, event_id, device.owner_id, device.device_id))
            return True

    @staticmethod
    def _event(row) -> NotificationEvent:
        return NotificationEvent(
            event_id=row["event_id"],
            sequence=row["sequence"],
            event_type=row["event_type"],
            payload=json.loads(row["payload_json"]),
            created_at=row["created_at"],
            expires_at=row["expires_at"],
            acknowledged_at=row["acknowledged_at"],
        )


class _Connection:
    def __init__(self, connection):
        self.connection = connection

    def __enter__(self):
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA busy_timeout=5000")
        self.connection.execute("PRAGMA foreign_keys=ON")
        return self.connection

    def __exit__(self, exc_type, exc, tb):
        try:
            if exc_type is None:
                self.connection.commit()
            else:
                self.connection.rollback()
        finally:
            self.connection.close()
        return False
