"""Single-use device pairing and rotating opaque credentials for Yuki sync.

Pairing-code creation is an administrative/local operation. Only the high-entropy
single-use code is presented to the public pairing endpoint. Store only SHA-256
digests of random 256-bit credentials; never log or persist raw credentials.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import secrets
import sqlite3
import uuid
from contextlib import closing
from typing import Any

from core.sync_store import SyncEventStore, SyncStoreError, UnknownDeviceError, RevokedDeviceError

ACCESS_TTL_SECONDS = 15 * 60
REFRESH_TTL_SECONDS = 30 * 24 * 60 * 60
DEFAULT_PAIRING_TTL_SECONDS = 5 * 60


class SyncAuthenticationError(ValueError):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class SyncCredentialStore:
    """Credential database sharing the SyncEventStore SQLite file."""

    def __init__(self, event_store: SyncEventStore):
        self.store = event_store
        with self.store._lock, closing(self.store._connect()) as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS sync_pairing_codes (
                    code_hash TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    consumed_at TEXT
                );
                CREATE TABLE IF NOT EXISTS sync_credentials (
                    token_hash TEXT PRIMARY KEY,
                    token_kind TEXT NOT NULL CHECK(token_kind IN ('access','refresh')),
                    device_id TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    revoked_at TEXT,
                    FOREIGN KEY(device_id) REFERENCES sync_devices(device_id)
                );
                CREATE INDEX IF NOT EXISTS idx_sync_credentials_device
                    ON sync_credentials(device_id, token_kind);
            """)

    def create_pairing_code(self, user_id: str, ttl_seconds: int = DEFAULT_PAIRING_TTL_SECONDS) -> str:
        if not isinstance(user_id, str) or not user_id or len(user_id) > 128:
            raise ValueError("Invalid user identifier.")
        if isinstance(ttl_seconds, bool) or not isinstance(ttl_seconds, int) or not 30 <= ttl_seconds <= 600:
            raise ValueError("Pairing code lifetime must be between 30 and 600 seconds.")
        code = secrets.token_urlsafe(32)
        expires = (_now() + timedelta(seconds=ttl_seconds)).isoformat()
        with self.store._lock, closing(self.store._connect()) as conn:
            conn.execute(
                "INSERT INTO sync_pairing_codes(code_hash,user_id,expires_at) VALUES(?,?,?)",
                (_digest(code), user_id, expires),
            )
        return code

    @staticmethod
    def _issue_pair(conn: sqlite3.Connection, device_id: str) -> dict[str, Any]:
        now = _now()
        access = secrets.token_urlsafe(32)
        refresh = secrets.token_urlsafe(48)
        conn.executemany(
            """INSERT INTO sync_credentials(token_hash,token_kind,device_id,expires_at,created_at)
               VALUES(?,?,?,?,?)""",
            [
                (_digest(access), "access", device_id, (now + timedelta(seconds=ACCESS_TTL_SECONDS)).isoformat(), now.isoformat()),
                (_digest(refresh), "refresh", device_id, (now + timedelta(seconds=REFRESH_TTL_SECONDS)).isoformat(), now.isoformat()),
            ],
        )
        return {
            "device_id": device_id,
            "access_token": access,
            "access_expires_in": ACCESS_TTL_SECONDS,
            "refresh_token": refresh,
            "refresh_expires_in": REFRESH_TTL_SECONDS,
            "token_type": "Bearer",
        }

    def consume_pairing_code(self, code: str, device_label: str = "Yuki device") -> dict[str, Any]:
        if not isinstance(code, str) or not 32 <= len(code) <= 128:
            raise SyncAuthenticationError("Pairing code is invalid or expired.")
        if not isinstance(device_label, str) or not device_label.strip() or len(device_label) > 80:
            raise ValueError("Device label must contain 1–80 characters.")
        code_hash = _digest(code)
        now = _now()
        device_id = uuid.uuid4().hex
        with self.store._lock, closing(self.store._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = conn.execute(
                    "SELECT user_id,expires_at,consumed_at FROM sync_pairing_codes WHERE code_hash=?",
                    (code_hash,),
                ).fetchone()
                if row is None or row["consumed_at"] is not None:
                    raise SyncAuthenticationError("Pairing code is invalid or expired.")
                if datetime.fromisoformat(row["expires_at"]) <= now:
                    raise SyncAuthenticationError("Pairing code is invalid or expired.")
                conn.execute(
                    "UPDATE sync_pairing_codes SET consumed_at=? WHERE code_hash=? AND consumed_at IS NULL",
                    (now.isoformat(), code_hash),
                )
                conn.execute(
                    "INSERT INTO sync_devices(device_id,user_id) VALUES(?,?)",
                    (device_id, row["user_id"]),
                )
                conn.execute(
                    "INSERT INTO sync_cursors(device_id,last_sequence) VALUES(?,0)",
                    (device_id,),
                )
                result = self._issue_pair(conn, device_id)
                conn.commit()
                return result
            except Exception:
                conn.rollback()
                raise

    def authenticate_access(self, access_token: str) -> str:
        if not isinstance(access_token, str) or not 32 <= len(access_token) <= 256:
            raise SyncAuthenticationError("Invalid access token.")
        with self.store._lock, closing(self.store._connect()) as conn:
            row = conn.execute(
                """SELECT c.device_id,c.expires_at,d.active
                   FROM sync_credentials c JOIN sync_devices d ON d.device_id=c.device_id
                   WHERE c.token_hash=? AND c.token_kind='access' AND c.revoked_at IS NULL""",
                (_digest(access_token),),
            ).fetchone()
            if row is None or not row["active"] or datetime.fromisoformat(row["expires_at"]) <= _now():
                raise SyncAuthenticationError("Invalid or expired access token.")
            return row["device_id"]

    def refresh(self, refresh_token: str) -> dict[str, Any]:
        if not isinstance(refresh_token, str) or not 32 <= len(refresh_token) <= 256:
            raise SyncAuthenticationError("Invalid refresh token.")
        now = _now()
        token_hash = _digest(refresh_token)
        with self.store._lock, closing(self.store._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = conn.execute(
                    """SELECT c.device_id,c.expires_at,c.revoked_at,d.active
                       FROM sync_credentials c JOIN sync_devices d ON d.device_id=c.device_id
                       WHERE c.token_hash=? AND c.token_kind='refresh'""",
                    (token_hash,),
                ).fetchone()
                if row is None:
                    raise SyncAuthenticationError("Invalid refresh token.")
                if row["revoked_at"] is not None:
                    # Reuse of a rotated refresh token indicates theft/replay.
                    conn.execute("UPDATE sync_devices SET active=0,revoked_at=? WHERE device_id=?", (now.isoformat(), row["device_id"]))
                    conn.execute("UPDATE sync_credentials SET revoked_at=? WHERE device_id=? AND revoked_at IS NULL", (now.isoformat(), row["device_id"]))
                    conn.commit()
                    raise SyncAuthenticationError("Refresh token reuse detected; device revoked.")
                if not row["active"] or datetime.fromisoformat(row["expires_at"]) <= now:
                    raise SyncAuthenticationError("Refresh token expired or device revoked.")
                # Revoke existing access credentials immediately on refresh.
                conn.execute(
                    """UPDATE sync_credentials SET revoked_at=?
                       WHERE device_id=? AND token_kind='access' AND revoked_at IS NULL""",
                    (now.isoformat(), row["device_id"]),
                )
                conn.execute("UPDATE sync_credentials SET revoked_at=? WHERE token_hash=?", (now.isoformat(), token_hash))
                result = self._issue_pair(conn, row["device_id"])
                conn.commit()
                return result
            except Exception:
                if conn.in_transaction:
                    conn.rollback()
                raise

    def revoke_device(self, device_id: str, user_id: str) -> None:
        now = _now().isoformat()
        with self.store._lock, closing(self.store._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            cur = conn.execute(
                "UPDATE sync_devices SET active=0,revoked_at=? WHERE device_id=? AND user_id=? AND active=1",
                (now, device_id, user_id),
            )
            if cur.rowcount != 1:
                conn.rollback()
                raise UnknownDeviceError("Active device was not found.")
            conn.execute(
                "UPDATE sync_credentials SET revoked_at=? WHERE device_id=? AND revoked_at IS NULL",
                (now, device_id),
            )
            conn.commit()
