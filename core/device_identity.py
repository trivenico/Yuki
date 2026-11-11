"""Owner-approved Ed25519 identity enrollment for paired devices.

This service stores public keys only. Device private keys are generated and
retained by the device. Enrollment proves possession of the private key using
a single-use, short-lived challenge bound to the owner and paired device.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import base64
import hashlib
import json
import re
import secrets
import sqlite3

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from core.device_pairing import DevicePairingService


_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{40,128}$")
_B64URL_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_DOMAIN = b"YUKI-DEVICE-IDENTITY-ENROLLMENT-V1\x00"
_KEY_DOMAIN = b"YUKI-ED25519-KEY-V1\x00"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _stamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _decode(value: str, *, expected_length: int) -> bytes:
    if (not isinstance(value, str) or not _B64URL_RE.fullmatch(value)
            or len(value) > 256):
        raise ValueError("invalid base64url value")
    try:
        decoded = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except Exception as exc:
        raise ValueError("invalid base64url value") from exc
    if len(decoded) != expected_length or _encode(decoded) != value:
        raise ValueError("invalid base64url value")
    return decoded


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("ascii")).hexdigest()


@dataclass(frozen=True)
class DeviceKeyChallenge:
    challenge_id: str
    nonce: str
    device_id: str
    key_fingerprint: str
    generation: int
    expires_at: str


@dataclass(frozen=True)
class DeviceIdentityKey:
    device_id: str
    key_id: str
    algorithm: str
    public_key: str
    fingerprint: str
    generation: int
    status: str
    created_at: str
    activated_at: str
    revoked_at: str | None


class DeviceIdentityError(PermissionError):
    """Generic invalid, expired, unauthorized, or reused identity challenge."""


class DeviceIdentityRegistry:
    """Persist owner-approved device public keys and verify Ed25519 possession."""

    def __init__(self, pairing: DevicePairingService, *, challenge_ttl_seconds: int = 180):
        if not isinstance(pairing, DevicePairingService):
            raise TypeError("pairing must be a DevicePairingService")
        if (isinstance(challenge_ttl_seconds, bool)
                or not isinstance(challenge_ttl_seconds, int)
                or not 30 <= challenge_ttl_seconds <= 600):
            raise ValueError("challenge_ttl_seconds must be between 30 and 600")
        self.pairing = pairing
        self.database_path = pairing.database_path
        self.challenge_ttl_seconds = challenge_ttl_seconds
        with self._connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("""
                CREATE TABLE IF NOT EXISTS device_identity_challenges (
                    challenge_hash TEXT PRIMARY KEY,
                    nonce_hash TEXT NOT NULL,
                    owner_id TEXT NOT NULL,
                    device_id TEXT NOT NULL,
                    key_fingerprint TEXT NOT NULL,
                    public_key TEXT NOT NULL,
                    generation INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    consumed_at TEXT
                )
            """)
            db.execute("""
                CREATE TABLE IF NOT EXISTS device_identity_keys (
                    device_id TEXT NOT NULL,
                    generation INTEGER NOT NULL,
                    key_id TEXT NOT NULL UNIQUE,
                    owner_id TEXT NOT NULL,
                    algorithm TEXT NOT NULL CHECK(algorithm='ed25519'),
                    public_key TEXT NOT NULL,
                    fingerprint TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('active','revoked','superseded')),
                    created_at TEXT NOT NULL,
                    activated_at TEXT NOT NULL,
                    revoked_at TEXT,
                    PRIMARY KEY(device_id, generation)
                )
            """)
            db.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS idx_device_identity_one_active
                ON device_identity_keys(device_id) WHERE status='active'
            """)
            db.execute("""
                CREATE INDEX IF NOT EXISTS idx_device_identity_owner
                ON device_identity_keys(owner_id, device_id, generation)
            """)
            # Device revocation invalidates both pending enrollments and active keys
            # in the same SQLite transaction as the existing bearer revocation.
            db.execute("""
                CREATE TRIGGER IF NOT EXISTS trg_revoke_device_identity
                AFTER UPDATE OF revoked_at ON paired_devices
                WHEN OLD.revoked_at IS NULL AND NEW.revoked_at IS NOT NULL
                BEGIN
                    UPDATE device_identity_keys
                    SET status='revoked', revoked_at=NEW.revoked_at
                    WHERE device_id=NEW.device_id AND status='active';
                    UPDATE device_identity_challenges
                    SET consumed_at=NEW.revoked_at
                    WHERE device_id=NEW.device_id AND consumed_at IS NULL;
                END
            """)

    def _connect(self):
        connection = sqlite3.connect(self.database_path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=5000")
        connection.execute("PRAGMA foreign_keys=ON")
        return _Connection(connection)

    @staticmethod
    def _owner(owner_id: str) -> str:
        if (not isinstance(owner_id, str) or not 1 <= len(owner_id) <= 128
                or not owner_id.strip()):
            raise ValueError("owner_id must be non-empty text of at most 128 characters")
        return owner_id

    @staticmethod
    def _device_id(device_id: str) -> str:
        if (not isinstance(device_id, str) or not 1 <= len(device_id) <= 128
                or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", device_id)):
            raise ValueError("invalid device_id")
        return device_id

    @staticmethod
    def _transcript(challenge_id: str, nonce: str, owner_id: str, device_id: str,
                    fingerprint: str, generation: int, expires_at: str) -> bytes:
        value = {
            "version": 1,
            "challenge_id": challenge_id,
            "nonce": nonce,
            "owner_id": owner_id,
            "device_id": device_id,
            "algorithm": "ed25519",
            "key_fingerprint": fingerprint,
            "generation": generation,
            "expires_at": expires_at,
        }
        canonical = json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
        ).encode("ascii")
        return _DOMAIN + canonical

    def create_challenge(self, owner_id: str, device_id: str, public_key: str,
                         *, owner_confirmed: bool) -> DeviceKeyChallenge:
        """Owner-authorized challenge creation binds approval to one exact public key."""
        owner_id = self._owner(owner_id)
        device_id = self._device_id(device_id)
        if owner_confirmed is not True:
            raise DeviceIdentityError("Owner approval is required.")
        public_bytes = _decode(public_key, expected_length=32)
        # Reject invalid Ed25519 encodings before issuing an approval challenge.
        Ed25519PublicKey.from_public_bytes(public_bytes)
        fingerprint = hashlib.sha256(_KEY_DOMAIN + public_bytes).hexdigest()
        challenge_id, nonce = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        now = _now()
        expires = now + timedelta(seconds=self.challenge_ttl_seconds)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if not self.pairing.owns_active_device_in_transaction(db, owner_id, device_id):
                raise DeviceIdentityError("Device is not active for this owner.")
            existing = db.execute("""
                SELECT 1 FROM device_identity_keys
                WHERE device_id=? AND status='active'
            """, (device_id,)).fetchone()
            if existing is not None:
                raise DeviceIdentityError("Device already has an active identity key.")
            generation = db.execute("""
                SELECT COALESCE(MAX(generation), 0) + 1 AS next_generation
                FROM device_identity_keys WHERE device_id=?
            """, (device_id,)).fetchone()["next_generation"]
            db.execute("""
                INSERT INTO device_identity_challenges(
                    challenge_hash,nonce_hash,owner_id,device_id,key_fingerprint,
                    public_key,generation,created_at,expires_at
                ) VALUES(?,?,?,?,?,?,?,?,?)
            """, (
                _digest(challenge_id), _digest(nonce), owner_id, device_id,
                fingerprint, public_key, generation, _stamp(now), _stamp(expires),
            ))
            db.execute("""
                DELETE FROM device_identity_challenges
                WHERE owner_id=? AND (expires_at<=? OR challenge_hash IN (
                    SELECT challenge_hash FROM device_identity_challenges
                    WHERE owner_id=? ORDER BY created_at DESC LIMIT -1 OFFSET 10
                ))
            """, (owner_id, _stamp(now), owner_id))
        return DeviceKeyChallenge(
            challenge_id=challenge_id, nonce=nonce, device_id=device_id,
            key_fingerprint=fingerprint, generation=generation,
            expires_at=_stamp(expires),
        )

    def activate_key(self, device_token: str, challenge_id: str, nonce: str,
                     signature: str) -> DeviceIdentityKey:
        """Consume a challenge once after paired-device auth and Ed25519 proof."""
        if not isinstance(device_token, str) or not _TOKEN_RE.fullmatch(device_token):
            raise DeviceIdentityError("Invalid or expired identity challenge.")
        if not isinstance(challenge_id, str) or not _TOKEN_RE.fullmatch(challenge_id):
            raise DeviceIdentityError("Invalid or expired identity challenge.")
        if not isinstance(nonce, str) or not _TOKEN_RE.fullmatch(nonce):
            raise DeviceIdentityError("Invalid or expired identity challenge.")
        try:
            signature_bytes = _decode(signature, expected_length=64)
        except ValueError as exc:
            raise DeviceIdentityError("Invalid or expired identity challenge.") from exc
        now = _now()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            device = self.pairing.authenticate_device_in_transaction(db, device_token)
            if device is None:
                raise DeviceIdentityError("Invalid or expired identity challenge.")
            row = db.execute("""
                SELECT * FROM device_identity_challenges
                WHERE challenge_hash=? AND device_id=? AND owner_id=?
            """, (_digest(challenge_id), device.device_id, device.owner_id)).fetchone()
            if (row is None or row["consumed_at"] is not None
                    or row["expires_at"] <= _stamp(now)
                    or not secrets.compare_digest(row["nonce_hash"], _digest(nonce))):
                raise DeviceIdentityError("Invalid or expired identity challenge.")
            if not self.pairing.owns_active_device_in_transaction(
                    db, device.owner_id, device.device_id):
                raise DeviceIdentityError("Invalid or expired identity challenge.")
            active = db.execute("""
                SELECT 1 FROM device_identity_keys
                WHERE device_id=? AND status='active'
            """, (device.device_id,)).fetchone()
            if active is not None:
                raise DeviceIdentityError("Invalid or expired identity challenge.")
            public_bytes = _decode(row["public_key"], expected_length=32)
            transcript = self._transcript(
                challenge_id, nonce, row["owner_id"], row["device_id"],
                row["key_fingerprint"], row["generation"], row["expires_at"],
            )
            try:
                Ed25519PublicKey.from_public_bytes(public_bytes).verify(
                    signature_bytes, transcript
                )
            except (InvalidSignature, ValueError) as exc:
                raise DeviceIdentityError("Invalid or expired identity challenge.") from exc
            consumed = db.execute("""
                UPDATE device_identity_challenges SET consumed_at=?
                WHERE challenge_hash=? AND consumed_at IS NULL AND expires_at>?
            """, (_stamp(now), _digest(challenge_id), _stamp(now))).rowcount
            if consumed != 1:
                raise DeviceIdentityError("Invalid or expired identity challenge.")
            db.execute("""
                INSERT INTO device_identity_keys(
                    device_id,generation,key_id,owner_id,algorithm,public_key,
                    fingerprint,status,created_at,activated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?)
            """, (
                device.device_id, row["generation"], row["key_fingerprint"],
                device.owner_id, "ed25519", row["public_key"],
                row["key_fingerprint"], "active", row["created_at"], _stamp(now),
            ))
            saved = db.execute("""
                SELECT * FROM device_identity_keys
                WHERE device_id=? AND generation=?
            """, (device.device_id, row["generation"])).fetchone()
        return self._key(saved)

    def get_active_key(self, owner_id: str, device_id: str) -> DeviceIdentityKey | None:
        owner_id = self._owner(owner_id)
        device_id = self._device_id(device_id)
        with self._connect() as db:
            row = db.execute("""
                SELECT k.* FROM device_identity_keys k
                JOIN paired_devices d ON d.device_id=k.device_id
                WHERE k.owner_id=? AND k.device_id=? AND k.status='active'
                  AND d.owner_id=k.owner_id AND d.revoked_at IS NULL
            """, (owner_id, device_id)).fetchone()
            return self._key(row) if row is not None else None

    @staticmethod
    def _key(row) -> DeviceIdentityKey:
        return DeviceIdentityKey(
            device_id=row["device_id"], key_id=row["key_id"],
            algorithm=row["algorithm"], public_key=row["public_key"],
            fingerprint=row["fingerprint"], generation=row["generation"],
            status=row["status"], created_at=row["created_at"],
            activated_at=row["activated_at"], revoked_at=row["revoked_at"],
        )


class _Connection:
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
