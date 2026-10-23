"""Authenticated request envelopes for future HTTPS sync transport.

No listener is opened here. This module provides HMAC request authentication,
timestamp freshness checks, and durable nonce replay protection. Confidentiality
must be provided by a correctly configured HTTPS/TLS transport; this module does
not implement cryptography for encryption.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import hmac
import json
import re
import sqlite3
import time
from pathlib import Path
from typing import Any, Mapping

_DEVICE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_NONCE_RE = re.compile(r"^[A-Za-z0-9_-]{20,128}$")
_METHOD_RE = re.compile(r"^[A-Z]{3,10}$")
_PATH_RE = re.compile(r"^/[A-Za-z0-9/_.,~!$&'()*+;=:@%-]{0,1023}$")
_MAX_BODY_BYTES = 64 * 1024
_DEFAULT_CLOCK_SKEW_SECONDS = 300


class TransportAuthError(ValueError):
    """Raised when an authenticated sync request fails validation."""


@dataclass(frozen=True)
class SignedRequest:
    device_id: str
    method: str
    path: str
    timestamp: int
    nonce: str
    body: bytes
    signature: str


def _canonical(device_id: str, method: str, path: str, timestamp: int, nonce: str, body: bytes) -> bytes:
    digest = hashlib.sha256(body).hexdigest()
    return "\n".join((device_id, method, path, str(timestamp), nonce, digest)).encode("utf-8")


def sign_request(*, device_id: str, method: str, path: str, timestamp: int,
                 nonce: str, body: bytes, credential: str) -> SignedRequest:
    _validate_fields(device_id, method, path, timestamp, nonce, body, credential)
    signature = hmac.new(credential.encode("utf-8"),
                         _canonical(device_id, method, path, timestamp, nonce, body),
                         hashlib.sha256).hexdigest()
    return SignedRequest(device_id, method, path, timestamp, nonce, body, signature)


def _validate_fields(device_id: Any, method: Any, path: Any, timestamp: Any,
                     nonce: Any, body: Any, credential: Any) -> None:
    if not isinstance(device_id, str) or not _DEVICE_RE.fullmatch(device_id):
        raise TransportAuthError("Invalid device ID.")
    if not isinstance(method, str) or not _METHOD_RE.fullmatch(method):
        raise TransportAuthError("Invalid HTTP method.")
    if not isinstance(path, str) or not _PATH_RE.fullmatch(path) or "//" in path or "/../" in path:
        raise TransportAuthError("Invalid request path.")
    if isinstance(timestamp, bool) or not isinstance(timestamp, int) or timestamp <= 0:
        raise TransportAuthError("Invalid request timestamp.")
    if not isinstance(nonce, str) or not _NONCE_RE.fullmatch(nonce):
        raise TransportAuthError("Invalid request nonce.")
    if not isinstance(body, bytes) or len(body) > _MAX_BODY_BYTES:
        raise TransportAuthError("Request body must be bytes within the size limit.")
    if not isinstance(credential, str) or not 32 <= len(credential) <= 512:
        raise TransportAuthError("Invalid device credential.")


class NonceReplayStore:
    """Durable replay guard keyed by device ID and nonce."""

    def __init__(self, database_path: str):
        if not isinstance(database_path, str) or not database_path.strip():
            raise ValueError("database_path must be a non-empty path.")
        self.database_path = str(Path(database_path).expanduser())
        Path(self.database_path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA synchronous=FULL")
            db.execute("""CREATE TABLE IF NOT EXISTS consumed_nonces (
                device_id TEXT NOT NULL,
                nonce TEXT NOT NULL,
                timestamp INTEGER NOT NULL,
                consumed_at INTEGER NOT NULL,
                PRIMARY KEY(device_id, nonce)
            )""")
            db.execute("CREATE INDEX IF NOT EXISTS idx_consumed_nonces_timestamp ON consumed_nonces(timestamp)")

    def _connect(self):
        db = sqlite3.connect(self.database_path, timeout=10)
        db.execute("PRAGMA busy_timeout=10000")
        return _ClosingConnection(db)

    def consume(self, device_id: str, nonce: str, timestamp: int, *, now: int | None = None,
                max_clock_skew_seconds: int = _DEFAULT_CLOCK_SKEW_SECONDS) -> None:
        if not isinstance(device_id, str) or not _DEVICE_RE.fullmatch(device_id):
            raise TransportAuthError("Invalid device ID.")
        if not isinstance(nonce, str) or not _NONCE_RE.fullmatch(nonce):
            raise TransportAuthError("Invalid nonce.")
        if isinstance(timestamp, bool) or not isinstance(timestamp, int) or timestamp <= 0:
            raise TransportAuthError("Invalid timestamp.")
        if isinstance(max_clock_skew_seconds, bool) or not isinstance(max_clock_skew_seconds, int) or not 1 <= max_clock_skew_seconds <= 3600:
            raise TransportAuthError("Invalid clock-skew policy.")
        now = int(time.time()) if now is None else now
        if isinstance(now, bool) or not isinstance(now, int) or abs(now - timestamp) > max_clock_skew_seconds:
            raise TransportAuthError("Request timestamp is outside the accepted window.")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            # Prune only expired replay records; current requests remain protected.
            db.execute("DELETE FROM consumed_nonces WHERE timestamp < ?", (now - max_clock_skew_seconds,))
            try:
                db.execute("INSERT INTO consumed_nonces(device_id, nonce, timestamp, consumed_at) VALUES(?, ?, ?, ?)",
                           (device_id, nonce, timestamp, now))
            except sqlite3.IntegrityError as exc:
                raise TransportAuthError("Replay detected: nonce already consumed.") from exc


def verify_request(request: SignedRequest, *, credential: str, replay_store: NonceReplayStore,
                   now: int | None = None,
                   max_clock_skew_seconds: int = _DEFAULT_CLOCK_SKEW_SECONDS) -> None:
    if not isinstance(request, SignedRequest):
        raise TypeError("request must be a SignedRequest")
    _validate_fields(request.device_id, request.method, request.path, request.timestamp,
                     request.nonce, request.body, credential)
    if not isinstance(request.signature, str) or not re.fullmatch(r"[0-9a-f]{64}", request.signature):
        raise TransportAuthError("Invalid request signature format.")
    expected = hmac.new(credential.encode("utf-8"),
                        _canonical(request.device_id, request.method, request.path,
                                   request.timestamp, request.nonce, request.body),
                        hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, request.signature):
        raise TransportAuthError("Request signature verification failed.")
    replay_store.consume(request.device_id, request.nonce, request.timestamp,
                         now=now, max_clock_skew_seconds=max_clock_skew_seconds)


def encode_request(request: SignedRequest) -> bytes:
    """Serialize an envelope for HTTPS; body is base64 to preserve exact bytes."""
    import base64
    envelope = {
        "device_id": request.device_id, "method": request.method, "path": request.path,
        "timestamp": request.timestamp, "nonce": request.nonce,
        "body_b64": base64.b64encode(request.body).decode("ascii"),
        "signature": request.signature,
    }
    raw = json.dumps(envelope, sort_keys=True, separators=(",", ":")).encode("utf-8")
    if len(raw) > _MAX_BODY_BYTES + 4096:
        raise TransportAuthError("Signed envelope exceeds size limit.")
    return raw


class _ClosingConnection:
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
