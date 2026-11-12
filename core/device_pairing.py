"""One-time, owner-bound pairing for trusted Yuki compute devices.

This service stores only hashes of pairing codes and device bearer tokens.
It does not expose a network listener; the embedding API must authenticate the
user, display the code securely, and deliver device tokens over TLS.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import re
import secrets
import sqlite3
import uuid
from pathlib import Path

from core.sqlite_file_security import prepare_private_sqlite_path


_NAME_RE = re.compile(r"^[^\x00-\x1f\x7f]{1,80}$")


def _now():
    return datetime.now(timezone.utc)


def _stamp(value):
    return value.astimezone(timezone.utc).isoformat()


def _digest(secret: str) -> str:
    return hashlib.sha256(secret.encode("ascii")).hexdigest()


@dataclass(frozen=True)
class PairingChallenge:
    code: str
    expires_at: str


@dataclass(frozen=True)
class PairedDevice:
    device_id: str
    owner_id: str
    name: str
    created_at: str
    last_seen_at: str | None


@dataclass(frozen=True)
class DeviceCredential:
    token: str
    device: PairedDevice


class DevicePairingError(PermissionError):
    """Generic invalid/expired/reused pairing or device credential."""


class DevicePairingService:
    """Issue short-lived, single-use pairing codes and revocable device tokens."""

    def __init__(self, database_path: str | Path, *, pairing_ttl_seconds: int = 180):
        if isinstance(pairing_ttl_seconds, bool) or not isinstance(pairing_ttl_seconds, int) or not 30 <= pairing_ttl_seconds <= 600:
            raise ValueError("pairing_ttl_seconds must be between 30 and 600")
        self.database_path = prepare_private_sqlite_path(database_path)
        self.pairing_ttl_seconds = pairing_ttl_seconds
        Path(self.database_path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("""
                CREATE TABLE IF NOT EXISTS device_pairing_challenges (
                    code_hash TEXT PRIMARY KEY,
                    owner_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    consumed_at TEXT
                )
            """)
            db.execute("""
                CREATE TABLE IF NOT EXISTS paired_devices (
                    device_id TEXT PRIMARY KEY,
                    owner_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    token_hash TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL,
                    last_seen_at TEXT,
                    revoked_at TEXT
                )
            """)
            db.execute("CREATE INDEX IF NOT EXISTS idx_paired_devices_owner ON paired_devices(owner_id, created_at)")

    def _connect(self):
        return _Connection(sqlite3.connect(self.database_path, timeout=5.0))

    @staticmethod
    def _owner(owner_id):
        if not isinstance(owner_id, str) or not 1 <= len(owner_id) <= 128 or not owner_id.strip():
            raise ValueError("owner_id must be non-empty text of at most 128 characters")
        return owner_id

    @staticmethod
    def _name(name):
        if not isinstance(name, str) or not _NAME_RE.fullmatch(name.strip()):
            raise ValueError("device name must contain 1-80 printable characters")
        return name.strip()

    @staticmethod
    def _device(row):
        return PairedDevice(
            device_id=row["device_id"], owner_id=row["owner_id"], name=row["name"],
            created_at=row["created_at"], last_seen_at=row["last_seen_at"],
        )

    def create_pairing_challenge(self, owner_id: str) -> PairingChallenge:
        """Create a one-time code; caller must show it only to the authenticated owner."""
        owner_id = self._owner(owner_id)
        code = secrets.token_urlsafe(32)
        now = _now()
        expires = now + timedelta(seconds=self.pairing_ttl_seconds)
        with self._connect() as db:
            db.execute(
                "INSERT INTO device_pairing_challenges(code_hash,owner_id,created_at,expires_at) VALUES(?,?,?,?)",
                (_digest(code), owner_id, _stamp(now), _stamp(expires)),
            )
            # Bound abandoned challenges per owner; expired rows are not credentials.
            db.execute("""
                DELETE FROM device_pairing_challenges
                WHERE owner_id=? AND (expires_at<=? OR code_hash IN (
                    SELECT code_hash FROM device_pairing_challenges
                    WHERE owner_id=? ORDER BY created_at DESC LIMIT -1 OFFSET 10
                ))
            """, (owner_id, _stamp(now), owner_id))
        return PairingChallenge(code=code, expires_at=_stamp(expires))

    def pair_device(self, code: str, name: str) -> DeviceCredential:
        """Consume a challenge exactly once and return a bearer token only once."""
        if not isinstance(code, str) or not 20 <= len(code) <= 128:
            raise DevicePairingError("Invalid or expired pairing challenge.")
        name = self._name(name)
        now = _now()
        code_hash = _digest(code)
        token = secrets.token_urlsafe(32)
        device_id = "dev_" + uuid.uuid4().hex
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("""
                SELECT owner_id, expires_at, consumed_at
                FROM device_pairing_challenges WHERE code_hash=?
            """, (code_hash,)).fetchone()
            if row is None or row["consumed_at"] is not None or row["expires_at"] <= _stamp(now):
                raise DevicePairingError("Invalid or expired pairing challenge.")
            changed = db.execute("""
                UPDATE device_pairing_challenges SET consumed_at=?
                WHERE code_hash=? AND consumed_at IS NULL AND expires_at>?
            """, (_stamp(now), code_hash, _stamp(now))).rowcount
            if changed != 1:
                raise DevicePairingError("Invalid or expired pairing challenge.")
            db.execute("""
                INSERT INTO paired_devices(device_id,owner_id,name,token_hash,created_at)
                VALUES(?,?,?,?,?)
            """, (device_id, row["owner_id"], name, _digest(token), _stamp(now)))
            device_row = db.execute(
                "SELECT * FROM paired_devices WHERE device_id=?", (device_id,)
            ).fetchone()
        return DeviceCredential(token=token, device=self._device(device_row))

    def authenticate_device_in_transaction(self, db, token: str) -> PairedDevice | None:
        """Resolve and touch a device using the caller's active SQLite transaction.

        Inference job claims/completions use this against the same database as
        revocation so credential validation and the state transition serialize.
        """
        if not isinstance(token, str) or not 20 <= len(token) <= 256:
            return None
        row = db.execute("""
            SELECT * FROM paired_devices
            WHERE token_hash=? AND revoked_at IS NULL
        """, (_digest(token),)).fetchone()
        if row is None:
            return None
        now = _stamp(_now())
        db.execute("UPDATE paired_devices SET last_seen_at=? WHERE device_id=? AND revoked_at IS NULL",
                   (now, row["device_id"]))
        updated = db.execute("SELECT * FROM paired_devices WHERE device_id=? AND revoked_at IS NULL",
                             (row["device_id"],)).fetchone()
        return self._device(updated) if updated is not None else None

    def authenticate_device(self, token: str) -> PairedDevice | None:
        """Resolve a device token without returning its stored hash or owner secrets."""
        with self._connect() as db:
            return self.authenticate_device_in_transaction(db, token)

    def owns_active_device_in_transaction(self, db, owner_id: str, device_id: str) -> bool:
        owner_id = self._owner(owner_id)
        if not isinstance(device_id, str) or not 1 <= len(device_id) <= 128:
            return False
        row = db.execute(
            "SELECT 1 FROM paired_devices WHERE device_id=? AND owner_id=? AND revoked_at IS NULL",
            (device_id, owner_id),
        ).fetchone()
        return row is not None

    def owns_active_device(self, owner_id: str, device_id: str) -> bool:
        """Check active device ownership using the pairing service's own database."""
        owner_id = self._owner(owner_id)
        if not isinstance(device_id, str) or not 1 <= len(device_id) <= 128:
            return False
        with self._connect() as db:
            row = db.execute(
                "SELECT 1 FROM paired_devices WHERE device_id=? AND owner_id=? AND revoked_at IS NULL",
                (device_id, owner_id),
            ).fetchone()
            return row is not None

    def list_devices(self, owner_id: str) -> list[PairedDevice]:
        owner_id = self._owner(owner_id)
        with self._connect() as db:
            rows = db.execute("""
                SELECT * FROM paired_devices
                WHERE owner_id=? AND revoked_at IS NULL ORDER BY created_at DESC LIMIT 100
            """, (owner_id,)).fetchall()
            return [self._device(row) for row in rows]

    def revoke_device(self, owner_id: str, device_id: str) -> bool:
        owner_id = self._owner(owner_id)
        if not isinstance(device_id, str) or not 1 <= len(device_id) <= 128:
            return False
        with self._connect() as db:
            changed = db.execute("""
                UPDATE paired_devices SET revoked_at=?
                WHERE owner_id=? AND device_id=? AND revoked_at IS NULL
            """, (_stamp(_now()), owner_id, device_id)).rowcount
            return changed == 1


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
