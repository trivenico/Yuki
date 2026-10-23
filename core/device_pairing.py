"""Local device-pairing primitives for Yuki's private sync boundary.

This module stores only hashes of pairing codes and device credentials. It does
not expose a network endpoint or send credentials to another device.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
from pathlib import Path
import secrets
import sqlite3
import re
from typing import Iterable

_DEVICE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_SCOPE_RE = re.compile(r"^[a-z][a-z0-9_.:-]{0,79}$")
_ALLOWED_SCOPES = frozenset({
    "sync.events.read",
    "sync.events.write",
    "sync.conflicts.read",
    "sync.conflicts.write",
})
_MAX_TTL_SECONDS = 15 * 60
_MAX_SCOPES = 16


class PairingError(ValueError):
    """Raised when pairing or device authorization validation fails."""


@dataclass(frozen=True)
class PairingChallenge:
    challenge_id: str
    code: str
    expires_at: str


@dataclass(frozen=True)
class PairedDevice:
    device_id: str
    scopes: tuple[str, ...]
    created_at: str
    revoked_at: str | None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _hash_secret(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def _validate_device_id(device_id: str) -> None:
    if not isinstance(device_id, str) or not _DEVICE_ID_RE.fullmatch(device_id):
        raise PairingError("Invalid device ID.")


def _validate_scopes(scopes: Iterable[str]) -> tuple[str, ...]:
    if isinstance(scopes, (str, bytes)):
        raise PairingError("Scopes must be an iterable of scope names, not a string.")
    try:
        values = tuple(sorted(set(scopes)))
    except (TypeError, ValueError) as exc:
        raise PairingError("Invalid scopes.") from exc
    if not values or len(values) > _MAX_SCOPES:
        raise PairingError("Between 1 and 16 scopes are required.")
    if any(not isinstance(s, str) or not _SCOPE_RE.fullmatch(s) or s not in _ALLOWED_SCOPES for s in values):
        raise PairingError("One or more scopes are not permitted.")
    return values


class DevicePairingStore:
    """SQLite-backed one-time pairing challenges and revocable device credentials."""

    def __init__(self, database_path: str):
        if not isinstance(database_path, str) or not database_path.strip():
            raise ValueError("database_path must be a non-empty path.")
        self.database_path = str(Path(database_path).expanduser())
        Path(self.database_path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA synchronous=FULL")
            db.execute("""CREATE TABLE IF NOT EXISTS pairing_challenges (
                challenge_id TEXT PRIMARY KEY,
                code_hash TEXT NOT NULL UNIQUE,
                expires_at TEXT NOT NULL,
                consumed_at TEXT
            )""")
            db.execute("""CREATE TABLE IF NOT EXISTS paired_devices (
                device_id TEXT PRIMARY KEY,
                credential_hash TEXT NOT NULL UNIQUE,
                scopes_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                revoked_at TEXT
            )""")
            db.execute("""CREATE TABLE IF NOT EXISTS pairing_audit (
                audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
                event TEXT NOT NULL,
                device_id TEXT,
                occurred_at TEXT NOT NULL,
                detail_json TEXT NOT NULL
            )""")

    def _connect(self):
        db = sqlite3.connect(self.database_path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=10000")
        return _ClosingConnection(db)

    def create_challenge(self, *, ttl_seconds: int = 300) -> PairingChallenge:
        if isinstance(ttl_seconds, bool) or not isinstance(ttl_seconds, int) or not 30 <= ttl_seconds <= _MAX_TTL_SECONDS:
            raise PairingError("Pairing challenge TTL must be 30 to 900 seconds.")
        code = secrets.token_urlsafe(32)
        challenge_id = secrets.token_urlsafe(18)
        now = _now()
        expires_at = _iso(now + timedelta(seconds=ttl_seconds))
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("INSERT INTO pairing_challenges(challenge_id, code_hash, expires_at) VALUES(?, ?, ?)",
                       (challenge_id, _hash_secret(code), expires_at))
            self._audit(db, "challenge_created", None, {"challenge_id": challenge_id, "expires_at": expires_at})
        return PairingChallenge(challenge_id, code, expires_at)

    def pair_device(self, *, challenge_id: str, code: str, device_id: str,
                    scopes: Iterable[str]) -> tuple[PairedDevice, str]:
        _validate_device_id(device_id)
        validated_scopes = _validate_scopes(scopes)
        if not isinstance(challenge_id, str) or not challenge_id or not isinstance(code, str) or not code:
            raise PairingError("A challenge ID and code are required.")
        credential = secrets.token_urlsafe(48)
        now = _now()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM pairing_challenges WHERE challenge_id=?", (challenge_id,)).fetchone()
            if row is None or row["consumed_at"] is not None:
                raise PairingError("Pairing challenge is unknown or already consumed.")
            if _parse_time(row["expires_at"]) <= now:
                raise PairingError("Pairing challenge has expired.")
            if not hmac.compare_digest(row["code_hash"], _hash_secret(code)):
                raise PairingError("Invalid pairing code.")
            existing = db.execute("SELECT device_id FROM paired_devices WHERE device_id=?", (device_id,)).fetchone()
            if existing:
                raise PairingError("Device ID already exists; revoke it before re-pairing.")
            db.execute("UPDATE pairing_challenges SET consumed_at=? WHERE challenge_id=?", (_iso(now), challenge_id))
            db.execute("INSERT INTO paired_devices(device_id, credential_hash, scopes_json, created_at) VALUES(?, ?, ?, ?)",
                       (device_id, _hash_secret(credential), json.dumps(validated_scopes), _iso(now)))
            self._audit(db, "device_paired", device_id, {"scopes": list(validated_scopes)})
        return PairedDevice(device_id, validated_scopes, _iso(now), None), credential

    def authenticate(self, *, device_id: str, credential: str, required_scope: str) -> PairedDevice:
        _validate_device_id(device_id)
        if not isinstance(credential, str) or not credential:
            raise PairingError("Device credential is required.")
        if not isinstance(required_scope, str) or required_scope not in _ALLOWED_SCOPES:
            raise PairingError("Requested scope is not permitted.")
        with self._connect() as db:
            row = db.execute("SELECT * FROM paired_devices WHERE device_id=?", (device_id,)).fetchone()
        if row is None or row["revoked_at"] is not None:
            raise PairingError("Device is unknown or revoked.")
        if not hmac.compare_digest(row["credential_hash"], _hash_secret(credential)):
            raise PairingError("Invalid device credential.")
        scopes = tuple(json.loads(row["scopes_json"]))
        if required_scope not in scopes:
            raise PairingError("Device is not authorized for this scope.")
        return PairedDevice(device_id, scopes, row["created_at"], row["revoked_at"])

    def revoke_device(self, device_id: str) -> PairedDevice:
        _validate_device_id(device_id)
        now = _iso(_now())
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM paired_devices WHERE device_id=?", (device_id,)).fetchone()
            if row is None:
                raise PairingError("Unknown device.")
            if row["revoked_at"] is None:
                db.execute("UPDATE paired_devices SET revoked_at=? WHERE device_id=?", (now, device_id))
                self._audit(db, "device_revoked", device_id, {})
                revoked_at = now
            else:
                revoked_at = row["revoked_at"]
        return PairedDevice(device_id, tuple(json.loads(row["scopes_json"])), row["created_at"], revoked_at)

    def list_devices(self, *, include_revoked: bool = False) -> list[PairedDevice]:
        if not isinstance(include_revoked, bool):
            raise PairingError("include_revoked must be a boolean.")
        query = "SELECT * FROM paired_devices" if include_revoked else "SELECT * FROM paired_devices WHERE revoked_at IS NULL"
        with self._connect() as db:
            rows = db.execute(query + " ORDER BY created_at, device_id").fetchall()
        return [PairedDevice(row["device_id"], tuple(json.loads(row["scopes_json"])),
                             row["created_at"], row["revoked_at"]) for row in rows]

    def audit_events(self, *, limit: int = 100) -> list[dict]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1000:
            raise PairingError("Audit limit must be 1 to 1000.")
        with self._connect() as db:
            rows = db.execute("SELECT event, device_id, occurred_at, detail_json FROM pairing_audit ORDER BY audit_id DESC LIMIT ?",
                              (limit,)).fetchall()
        return [{"event": r["event"], "device_id": r["device_id"], "occurred_at": r["occurred_at"],
                 "details": json.loads(r["detail_json"])} for r in rows]

    @staticmethod
    def _audit(db, event: str, device_id: str | None, details: dict) -> None:
        db.execute("INSERT INTO pairing_audit(event, device_id, occurred_at, detail_json) VALUES(?, ?, ?, ?)",
                   (event, device_id, _iso(_now()), json.dumps(details, sort_keys=True)))


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise PairingError("Stored timestamp must be timezone-aware.")
    return parsed.astimezone(timezone.utc)


class _ClosingConnection:
    """Small context manager that always closes sqlite connections."""
    def __init__(self, connection):
        self.connection = connection

    def __enter__(self):
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
