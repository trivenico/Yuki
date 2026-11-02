"""Revocable per-installation credentials for the Yuki Cloud Gateway.

Device tokens are high-entropy opaque secrets. Only SHA-256 digests are stored.
Issuance and revocation must be exposed only through authenticated owner/admin
routes; this module deliberately contains no public self-registration path.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import secrets
import sqlite3
import threading


def _now():
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class IssuedDeviceCredential:
    device_id: str
    device_name: str
    token: str
    created_at: str


@dataclass(frozen=True)
class DevicePrincipal:
    device_id: str
    user_id: str
    device_name: str
    scopes: frozenset[str]


class DeviceCredentialError(PermissionError):
    """Generic invalid, revoked, or expired device credential."""


class DeviceCredentialStore:
    """SQLite-backed device identity registry with one-way token storage."""

    ALLOWED_SCOPES = frozenset({
        "profile:read",
        "chat:write",
        "tasks:read",
        "tasks:write",
        "devices:read",
    })

    def __init__(self, database_path):
        if not isinstance(database_path, str) or not database_path.strip():
            raise ValueError("database_path must be a non-empty path.")
        self.database_path = str(Path(database_path).expanduser())
        Path(self.database_path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        with self._connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("""
                CREATE TABLE IF NOT EXISTS gateway_devices (
                    device_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    device_name TEXT NOT NULL,
                    token_hash TEXT NOT NULL UNIQUE,
                    scopes_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    last_seen_at TEXT,
                    revoked_at TEXT
                )
            """)
            db.execute("CREATE INDEX IF NOT EXISTS idx_gateway_devices_user ON gateway_devices(user_id)")

    @contextmanager
    def _connect(self):
        """Yield a configured SQLite connection and always close its handle.

        sqlite3.Connection.__exit__ commits or rolls back but does not close
        the connection. Explicit closure is important on Windows, where open
        SQLite handles prevent temporary database files from being removed.
        """
        connection = sqlite3.connect(self.database_path, timeout=5.0)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys=ON")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _token_digest(token):
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    @staticmethod
    def _validate_device_name(value):
        if not isinstance(value, str):
            raise ValueError("Device name must be text.")
        value = " ".join(value.split())
        if not 1 <= len(value) <= 80:
            raise ValueError("Device name must be 1-80 characters.")
        return value

    @staticmethod
    def _validate_user_id(value):
        if not isinstance(value, str) or not 1 <= len(value) <= 128:
            raise ValueError("A valid authenticated user_id is required.")
        return value

    @classmethod
    def _validate_scopes(cls, scopes):
        if isinstance(scopes, (str, bytes)):
            raise ValueError("Scopes must be a collection of scope names.")
        try:
            result = frozenset(scopes)
        except TypeError as exc:
            raise ValueError("Scopes must be a collection of scope names.") from exc
        if not result or not result.issubset(cls.ALLOWED_SCOPES):
            raise ValueError("Device scopes are empty or contain unsupported permissions.")
        return result

    def issue(self, *, user_id, device_name, scopes=("profile:read", "chat:write", "tasks:read", "tasks:write")):
        """Issue a token once. The raw secret is returned only in this result."""
        user_id = self._validate_user_id(user_id)
        device_name = self._validate_device_name(device_name)
        scopes = self._validate_scopes(scopes)
        token = "yuki_dev_" + secrets.token_urlsafe(32)
        device_id = "yd_" + secrets.token_urlsafe(18)
        created_at = _now()
        with self._lock, self._connect() as db:
            db.execute(
                """INSERT INTO gateway_devices
                   (device_id,user_id,device_name,token_hash,scopes_json,created_at)
                   VALUES(?,?,?,?,?,?)""",
                (
                    device_id, user_id, device_name, self._token_digest(token),
                    __import__("json").dumps(sorted(scopes)), created_at,
                ),
            )
        return IssuedDeviceCredential(device_id, device_name, token, created_at)

    def authenticate(self, token):
        if not isinstance(token, str) or not token.startswith("yuki_dev_") or len(token) > 256:
            raise DeviceCredentialError("Invalid device credential.")
        digest = self._token_digest(token)
        with self._lock, self._connect() as db:
            row = db.execute(
                """SELECT device_id,user_id,device_name,scopes_json
                   FROM gateway_devices WHERE token_hash=? AND revoked_at IS NULL""",
                (digest,),
            ).fetchone()
            if row is None:
                raise DeviceCredentialError("Invalid device credential.")
            db.execute(
                "UPDATE gateway_devices SET last_seen_at=? WHERE device_id=? AND revoked_at IS NULL",
                (_now(), row["device_id"]),
            )
        import json
        try:
            scopes = frozenset(json.loads(row["scopes_json"]))
        except (TypeError, ValueError):
            raise DeviceCredentialError("Invalid device credential.") from None
        if not scopes or not scopes.issubset(self.ALLOWED_SCOPES):
            raise DeviceCredentialError("Invalid device credential.")
        return DevicePrincipal(row["device_id"], row["user_id"], row["device_name"], scopes)

    def revoke(self, *, user_id, device_id):
        user_id = self._validate_user_id(user_id)
        if not isinstance(device_id, str) or not device_id.startswith("yd_"):
            return False
        with self._lock, self._connect() as db:
            result = db.execute(
                """UPDATE gateway_devices SET revoked_at=?
                   WHERE device_id=? AND user_id=? AND revoked_at IS NULL""",
                (_now(), device_id, user_id),
            )
            return result.rowcount == 1

    def list_for_user(self, *, user_id):
        user_id = self._validate_user_id(user_id)
        with self._connect() as db:
            rows = db.execute(
                """SELECT device_id,device_name,created_at,last_seen_at,revoked_at
                   FROM gateway_devices WHERE user_id=? ORDER BY created_at DESC""",
                (user_id,),
            ).fetchall()
        return [dict(row) for row in rows]
