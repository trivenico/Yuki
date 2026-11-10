"""Durable, owner/session-bound OAuth transaction storage."""
from __future__ import annotations
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
import hmac
from pathlib import Path
import sqlite3
import time
from typing import Callable


class OAuthTransactionError(ValueError):
    """Stable OAuth transaction validation or lifecycle failure."""


@dataclass(frozen=True)
class OAuthTransaction:
    state: str
    code_verifier: str
    redirect_uri: str
    issuer: str
    resource: str
    expires_at: int


class SQLiteOAuthTransactionStore:
    """One-time state store; PKCE verifier is encrypted by an injected host cipher."""

    def __init__(self, database_path: str, *, hmac_key: bytes,
                 encrypt: Callable[[bytes], bytes] | None = None, decrypt: Callable[[bytes], bytes] | None = None,
                 encrypt_with_context: Callable[[bytes, bytes], bytes] | None = None,
                 decrypt_with_context: Callable[[bytes, bytes], bytes] | None = None,
                 clock: Callable[[], float] = time.time, max_ttl_seconds: int = 900):
        if not isinstance(database_path, str) or not database_path.strip():
            raise ValueError("database_path must be a non-empty path")
        if not isinstance(hmac_key, bytes) or len(hmac_key) < 32:
            raise ValueError("hmac_key must contain at least 32 bytes")
        legacy_pair = callable(encrypt) and callable(decrypt)
        contextual_pair = callable(encrypt_with_context) and callable(decrypt_with_context)
        if not (legacy_pair or contextual_pair):
            raise ValueError("trusted encryption and decryption callbacks are required")
        if (encrypt is None) != (decrypt is None):
            raise ValueError("legacy encryption callbacks must be supplied together")
        if (encrypt_with_context is None) != (decrypt_with_context is None):
            raise ValueError("contextual encryption callbacks must be supplied together")
        if encrypt_with_context is not None and (not callable(encrypt_with_context) or not callable(decrypt_with_context)):
            raise ValueError("invalid contextual encryption callbacks")
        if type(max_ttl_seconds) is not int or not 60 <= max_ttl_seconds <= 1800:
            raise ValueError("max_ttl_seconds must be between 60 and 1800")
        self.database_path = str(Path(database_path).expanduser())
        Path(self.database_path).parent.mkdir(parents=True, exist_ok=True)
        self._hmac_key, self._encrypt, self._decrypt = bytes(hmac_key), encrypt, decrypt
        self._encrypt_with_context = encrypt_with_context
        self._decrypt_with_context = decrypt_with_context
        self._clock, self.max_ttl_seconds = clock, max_ttl_seconds
        with self._connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS oauth_transactions (
                state_digest TEXT PRIMARY KEY, owner_digest TEXT NOT NULL,
                session_digest TEXT NOT NULL, verifier_ciphertext BLOB NOT NULL,
                redirect_uri TEXT NOT NULL, issuer TEXT NOT NULL, resource TEXT NOT NULL,
                created_at INTEGER NOT NULL, expires_at INTEGER NOT NULL, consumed_at INTEGER
            )""")
            db.execute("CREATE INDEX IF NOT EXISTS idx_oauth_transactions_expiry ON oauth_transactions(expires_at, consumed_at)")

    def _digest(self, label: str, value: str) -> str:
        return hmac.new(self._hmac_key, (label + chr(0) + value).encode("utf-8"), hashlib.sha256).hexdigest()

    @staticmethod
    def _bounded(value, *, label: str, maximum: int) -> str:
        if not isinstance(value, str) or not value or len(value) > maximum or any(ord(c) < 0x20 for c in value):
            raise OAuthTransactionError("invalid_" + label)
        return value

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.database_path, timeout=5.0, isolation_level=None)
        try:
            db.execute("PRAGMA busy_timeout = 5000")
            yield db
        finally:
            db.close()

    @staticmethod
    def _cipher_context(state_digest: str, owner_digest: str, session_digest: str) -> bytes:
        return json.dumps([1, state_digest, owner_digest, session_digest], separators=(",", ":")).encode("ascii")

    def create(self, *, state: str, code_verifier: str, owner_id: str, session_id: str,
               redirect_uri: str, issuer: str, resource: str, ttl_seconds: int = 600) -> int:
        state = self._bounded(state, label="state", maximum=256)
        verifier = self._bounded(code_verifier, label="code_verifier", maximum=128)
        owner = self._bounded(owner_id, label="owner", maximum=256)
        session = self._bounded(session_id, label="session", maximum=256)
        redirect = self._bounded(redirect_uri, label="redirect_uri", maximum=2048)
        issuer_value = self._bounded(issuer, label="issuer", maximum=2048)
        resource_value = self._bounded(resource, label="resource", maximum=2048)
        if type(ttl_seconds) is not int or not 60 <= ttl_seconds <= self.max_ttl_seconds:
            raise OAuthTransactionError("invalid_transaction_ttl")
        state_digest = self._digest("state", state)
        owner_digest = self._digest("owner", owner)
        session_digest = self._digest("session", session)
        try:
            if self._encrypt_with_context is not None:
                encrypted = self._encrypt_with_context(
                    verifier.encode("utf-8"),
                    self._cipher_context(state_digest, owner_digest, session_digest),
                )
            else:
                encrypted = self._encrypt(verifier.encode("utf-8"))
        except Exception:
            raise OAuthTransactionError("verifier_encryption_failed") from None
        if not isinstance(encrypted, bytes) or not encrypted or len(encrypted) > 16384:
            raise OAuthTransactionError("invalid_encrypted_verifier")
        now = int(self._clock())
        try:
            with self._connect() as db:
                db.execute("BEGIN IMMEDIATE")
                try:
                    db.execute("""INSERT INTO oauth_transactions
                        (state_digest, owner_digest, session_digest, verifier_ciphertext,
                         redirect_uri, issuer, resource, created_at, expires_at, consumed_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)""",
                        (state_digest, owner_digest, session_digest, sqlite3.Binary(encrypted),
                         redirect, issuer_value, resource_value, now, now + ttl_seconds))
                    db.execute("COMMIT")
                except Exception:
                    db.execute("ROLLBACK")
                    raise
        except sqlite3.IntegrityError:
            raise OAuthTransactionError("duplicate_transaction_state") from None
        return now + ttl_seconds

    def consume(self, *, state: str, owner_id: str, session_id: str) -> OAuthTransaction:
        state = self._bounded(state, label="state", maximum=256)
        owner = self._bounded(owner_id, label="owner", maximum=256)
        session = self._bounded(session_id, label="session", maximum=256)
        state_digest = self._digest("state", state)
        owner_digest = self._digest("owner", owner)
        session_digest = self._digest("session", session)
        now = int(self._clock())
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                row = db.execute("""SELECT owner_digest, session_digest, verifier_ciphertext,
                    redirect_uri, issuer, resource, expires_at, consumed_at
                    FROM oauth_transactions WHERE state_digest = ?""", (state_digest,)).fetchone()
                if row is None:
                    raise OAuthTransactionError("transaction_not_found")
                stored_owner, stored_session, ciphertext, redirect, issuer, resource, expires, consumed = row
                if not hmac.compare_digest(stored_owner, owner_digest):
                    raise OAuthTransactionError("transaction_owner_mismatch")
                if not hmac.compare_digest(stored_session, session_digest):
                    raise OAuthTransactionError("transaction_session_mismatch")
                if consumed is not None:
                    raise OAuthTransactionError("transaction_already_consumed")
                if now >= expires:
                    db.execute("UPDATE oauth_transactions SET consumed_at = ? WHERE state_digest = ?", (now, state_digest))
                    db.execute("COMMIT")
                    raise OAuthTransactionError("transaction_expired")
                cursor = db.execute("""UPDATE oauth_transactions SET consumed_at = ?
                    WHERE state_digest = ? AND consumed_at IS NULL AND expires_at > ?""",
                    (now, state_digest, now))
                if cursor.rowcount != 1:
                    raise OAuthTransactionError("transaction_already_consumed")
                db.execute("COMMIT")
            except OAuthTransactionError:
                if db.in_transaction:
                    db.execute("ROLLBACK")
                raise
            except Exception:
                if db.in_transaction:
                    db.execute("ROLLBACK")
                raise
        try:
            if self._decrypt_with_context is not None:
                plaintext = self._decrypt_with_context(
                    bytes(ciphertext),
                    self._cipher_context(state_digest, owner_digest, session_digest),
                )
            else:
                plaintext = self._decrypt(bytes(ciphertext))
            verifier = plaintext.decode("utf-8")
        except Exception:
            raise OAuthTransactionError("verifier_decryption_failed") from None
        if not verifier or len(verifier) > 128:
            raise OAuthTransactionError("invalid_decrypted_verifier")
        return OAuthTransaction(state, verifier, redirect, issuer, resource, expires)

    def purge_expired(self, *, retention_seconds: int = 86400) -> int:
        if type(retention_seconds) is not int or not 0 <= retention_seconds <= 604800:
            raise ValueError("retention_seconds must be between 0 and 604800")
        cutoff = int(self._clock()) - retention_seconds
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                cursor = db.execute("DELETE FROM oauth_transactions WHERE expires_at <= ?", (cutoff,))
                count = cursor.rowcount
                db.execute("COMMIT")
                return count
            except Exception:
                db.execute("ROLLBACK")
                raise
