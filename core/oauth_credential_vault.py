"""Encrypted, owner-scoped OAuth credential storage.

Cryptography and key custody are deliberately delegated to a trusted host-provided
AEAD cipher. This module never accepts raw keys and never falls back to plaintext.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
import hashlib
import hmac
import json
from pathlib import Path

from core.sqlite_file_security import prepare_private_sqlite_path
import re
import sqlite3
import time
from typing import Callable, Protocol


class OAuthCredentialError(ValueError):
    """Stable, non-sensitive credential vault failure."""


class CredentialCipher(Protocol):
    """Host-managed authenticated encryption with versioned key identifiers."""

    def encrypt(self, plaintext: bytes, *, associated_data: bytes) -> tuple[str, bytes]: ...
    def decrypt(self, key_id: str, ciphertext: bytes, *, associated_data: bytes) -> bytes: ...


@dataclass(frozen=True)
class StoredOAuthCredential:
    owner_id: str
    provider_id: str
    account_id: str
    access_token: str = field(repr=False)
    expires_at: int
    scopes: tuple[str, ...]
    version: int
    connected_at: int
    refresh_token: str | None = field(default=None, repr=False)


_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:@+-]{0,255}$")
_TOKEN = re.compile(r"^[A-Za-z0-9._~+/-]{1,8192}={0,}$")
_SCOPE = re.compile(r"^[\x21\x23-\x5B\x5D-\x7E]{1,128}$")


class SQLiteOAuthCredentialVault:
    """SQLite metadata plus host-AEAD encrypted token values.

    The HMAC key is a separate host-managed secret used only for refresh-token
    compare-and-swap fingerprints. It must be stable across restarts and distinct
    from the AEAD key. Cipher implementations must bind associated_data as AEAD AAD.
    """

    def __init__(
        self,
        database_path: str,
        *,
        cipher: CredentialCipher,
        hmac_key: bytes,
        clock: Callable[[], float] = time.time,
    ):
        if not isinstance(database_path, str) or not database_path.strip():
            raise ValueError("database_path must be a non-empty path")
        if not callable(getattr(cipher, "encrypt", None)) or not callable(getattr(cipher, "decrypt", None)):
            raise ValueError("a trusted authenticated-encryption cipher is required")
        if not isinstance(hmac_key, bytes) or len(hmac_key) < 32:
            raise ValueError("hmac_key must contain at least 32 bytes")
        if not callable(clock):
            raise ValueError("clock must be callable")
        self.database_path = prepare_private_sqlite_path(database_path)
        self._cipher = cipher
        self._hmac_key = bytes(hmac_key)
        self._clock = clock
        with self._connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("""CREATE TABLE IF NOT EXISTS oauth_credentials (
                owner_id TEXT NOT NULL,
                provider_id TEXT NOT NULL,
                account_id TEXT NOT NULL,
                access_key_id TEXT NOT NULL,
                access_ciphertext BLOB NOT NULL,
                refresh_key_id TEXT,
                refresh_ciphertext BLOB,
                refresh_digest TEXT,
                expires_at INTEGER NOT NULL,
                scopes_json TEXT NOT NULL,
                version INTEGER NOT NULL CHECK(version > 0),
                connected_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                PRIMARY KEY(owner_id, provider_id, account_id)
            )""")
            db.execute("CREATE INDEX IF NOT EXISTS idx_oauth_credentials_owner ON oauth_credentials(owner_id, provider_id)")

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.database_path, timeout=5.0, isolation_level=None)
        try:
            db.execute("PRAGMA busy_timeout=5000")
            yield db
        finally:
            db.close()

    @staticmethod
    def _identifier(value: str, label: str) -> str:
        if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
            raise OAuthCredentialError("invalid_" + label)
        return value

    @staticmethod
    def _token(value: str | None, label: str, *, required: bool) -> str | None:
        if value is None and not required:
            return None
        if not isinstance(value, str) or not _TOKEN.fullmatch(value):
            raise OAuthCredentialError("invalid_" + label)
        return value

    @staticmethod
    def _scopes(values) -> tuple[str, ...]:
        if not isinstance(values, (tuple, list)) or len(values) > 64:
            raise OAuthCredentialError("invalid_scopes")
        scopes = tuple(values)
        if any(not isinstance(s, str) or not _SCOPE.fullmatch(s) for s in scopes):
            raise OAuthCredentialError("invalid_scopes")
        if len(set(scopes)) != len(scopes):
            raise OAuthCredentialError("duplicate_scopes")
        return tuple(sorted(scopes))

    @staticmethod
    def _aad(owner: str, provider: str, account: str, token_kind: str) -> bytes:
        return json.dumps(
            [1, owner, provider, account, token_kind],
            ensure_ascii=True, separators=(",", ":"),
        ).encode("ascii")

    def _encrypt(self, token: str, aad: bytes) -> tuple[str, bytes]:
        try:
            key_id, ciphertext = self._cipher.encrypt(token.encode("utf-8"), associated_data=aad)
        except Exception:
            raise OAuthCredentialError("credential_encryption_failed") from None
        if not isinstance(key_id, str) or not _IDENTIFIER.fullmatch(key_id):
            raise OAuthCredentialError("invalid_encryption_key_id")
        if not isinstance(ciphertext, bytes) or not 16 <= len(ciphertext) <= 16384:
            raise OAuthCredentialError("invalid_encrypted_credential")
        return key_id, ciphertext

    def _decrypt(self, key_id: str, ciphertext: bytes, aad: bytes, label: str) -> str:
        try:
            value = self._cipher.decrypt(key_id, bytes(ciphertext), associated_data=aad)
            token = value.decode("utf-8")
        except Exception:
            raise OAuthCredentialError(label + "_decryption_failed") from None
        if not _TOKEN.fullmatch(token):
            raise OAuthCredentialError("invalid_decrypted_" + label)
        return token

    def _refresh_digest(self, token: str | None) -> str | None:
        if token is None:
            return None
        return hmac.new(self._hmac_key, b"oauth-refresh\\0" + token.encode("ascii"), hashlib.sha256).hexdigest()

    def store(
        self, *, owner_id: str, provider_id: str, account_id: str,
        access_token: str, refresh_token: str | None, expires_at: int,
        scopes,
    ) -> int:
        owner = self._identifier(owner_id, "owner_id")
        provider = self._identifier(provider_id, "provider_id")
        account = self._identifier(account_id, "account_id")
        access = self._token(access_token, "access_token", required=True)
        refresh = self._token(refresh_token, "refresh_token", required=False)
        if type(expires_at) is not int or not int(self._clock()) < expires_at <= int(self._clock()) + 31_536_000:
            raise OAuthCredentialError("invalid_token_expiry")
        normalized_scopes = self._scopes(scopes)
        access_key, access_cipher = self._encrypt(access, self._aad(owner, provider, account, "access"))
        refresh_key, refresh_cipher = (None, None)
        if refresh is not None:
            refresh_key, refresh_cipher = self._encrypt(refresh, self._aad(owner, provider, account, "refresh"))
        now = int(self._clock())
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                row = db.execute(
                    "SELECT version FROM oauth_credentials WHERE owner_id=? AND provider_id=? AND account_id=?",
                    (owner, provider, account),
                ).fetchone()
                if row:
                    raise OAuthCredentialError("credential_already_exists")
                db.execute("""INSERT INTO oauth_credentials
                    (owner_id,provider_id,account_id,access_key_id,access_ciphertext,
                     refresh_key_id,refresh_ciphertext,refresh_digest,expires_at,scopes_json,
                     version,connected_at,updated_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,1,?,?)""",
                    (owner, provider, account, access_key, sqlite3.Binary(access_cipher),
                     refresh_key, sqlite3.Binary(refresh_cipher) if refresh_cipher else None,
                     self._refresh_digest(refresh), expires_at,
                     json.dumps(normalized_scopes, separators=(",", ":")), now, now))
                db.execute("COMMIT")
                return 1
            except Exception:
                if db.in_transaction:
                    db.execute("ROLLBACK")
                raise

    def get(self, *, owner_id: str, provider_id: str, account_id: str) -> StoredOAuthCredential | None:
        owner = self._identifier(owner_id, "owner_id")
        provider = self._identifier(provider_id, "provider_id")
        account = self._identifier(account_id, "account_id")
        with self._connect() as db:
            row = db.execute("""SELECT access_key_id,access_ciphertext,refresh_key_id,
                refresh_ciphertext,expires_at,scopes_json,version,connected_at
                FROM oauth_credentials WHERE owner_id=? AND provider_id=? AND account_id=?""",
                (owner, provider, account)).fetchone()
        if row is None:
            return None
        access = self._decrypt(row[0], row[1], self._aad(owner, provider, account, "access"), "access_token")
        refresh = None
        if row[2] is not None and row[3] is not None:
            refresh = self._decrypt(row[2], row[3], self._aad(owner, provider, account, "refresh"), "refresh_token")
        try:
            scopes = self._scopes(json.loads(row[5]))
        except (TypeError, ValueError):
            raise OAuthCredentialError("invalid_stored_scopes") from None
        return StoredOAuthCredential(
            owner_id=owner, provider_id=provider, account_id=account,
            access_token=access, expires_at=row[4], scopes=scopes,
            version=row[6], connected_at=row[7], refresh_token=refresh,
        )

    def rotate(
        self, *, owner_id: str, provider_id: str, account_id: str,
        expected_version: int, expected_refresh_token: str,
        access_token: str, refresh_token: str | None, expires_at: int, scopes,
    ) -> int:
        """Atomically replace a grant only if the refresh-token generation is current."""
        owner = self._identifier(owner_id, "owner_id")
        provider = self._identifier(provider_id, "provider_id")
        account = self._identifier(account_id, "account_id")
        old_refresh = self._token(expected_refresh_token, "expected_refresh_token", required=True)
        access = self._token(access_token, "access_token", required=True)
        refresh = self._token(refresh_token, "refresh_token", required=False)
        if type(expected_version) is not int or expected_version < 1:
            raise OAuthCredentialError("invalid_expected_version")
        if type(expires_at) is not int or not int(self._clock()) < expires_at <= int(self._clock()) + 31_536_000:
            raise OAuthCredentialError("invalid_token_expiry")
        normalized_scopes = self._scopes(scopes)
        access_key, access_cipher = self._encrypt(access, self._aad(owner, provider, account, "access"))
        refresh_key, refresh_cipher = (None, None)
        if refresh is not None:
            refresh_key, refresh_cipher = self._encrypt(refresh, self._aad(owner, provider, account, "refresh"))
        now = int(self._clock())
        expected_digest = self._refresh_digest(old_refresh)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                row = db.execute("""SELECT version,refresh_digest FROM oauth_credentials
                    WHERE owner_id=? AND provider_id=? AND account_id=?""",
                    (owner, provider, account)).fetchone()
                if row is None:
                    raise OAuthCredentialError("credential_not_found")
                if row[0] != expected_version or row[1] is None or not hmac.compare_digest(row[1], expected_digest):
                    raise OAuthCredentialError("stale_refresh_generation")
                # OAuth refresh responses may omit refresh_token. Retain the
                # current token rather than accidentally disabling future refresh.
                if refresh is None:
                    refresh = old_refresh
                    refresh_key, refresh_cipher = self._encrypt(
                        refresh, self._aad(owner, provider, account, "refresh")
                    )
                next_version = expected_version + 1
                db.execute("""UPDATE oauth_credentials SET access_key_id=?,access_ciphertext=?,
                    refresh_key_id=?,refresh_ciphertext=?,refresh_digest=?,expires_at=?,scopes_json=?,
                    version=?,updated_at=? WHERE owner_id=? AND provider_id=? AND account_id=? AND version=?""",
                    (access_key, sqlite3.Binary(access_cipher),
                     refresh_key, sqlite3.Binary(refresh_cipher) if refresh_cipher else None,
                     self._refresh_digest(refresh), expires_at,
                     json.dumps(normalized_scopes, separators=(",", ":")), next_version, now,
                     owner, provider, account, expected_version))
                if db.execute("SELECT changes()").fetchone()[0] != 1:
                    raise OAuthCredentialError("stale_refresh_generation")
                db.execute("COMMIT")
                return next_version
            except Exception:
                if db.in_transaction:
                    db.execute("ROLLBACK")
                raise

    def disconnect(
        self, *, owner_id: str, provider_id: str, account_id: str,
        expected_version: int | None = None,
    ) -> bool:
        owner = self._identifier(owner_id, "owner_id")
        provider = self._identifier(provider_id, "provider_id")
        account = self._identifier(account_id, "account_id")
        if expected_version is not None and (type(expected_version) is not int or expected_version < 1):
            raise OAuthCredentialError("invalid_expected_version")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                if expected_version is None:
                    cursor = db.execute(
                        "DELETE FROM oauth_credentials WHERE owner_id=? AND provider_id=? AND account_id=?",
                        (owner, provider, account),
                    )
                else:
                    cursor = db.execute(
                        "DELETE FROM oauth_credentials WHERE owner_id=? AND provider_id=? AND account_id=? AND version=?",
                        (owner, provider, account, expected_version),
                    )
                db.execute("COMMIT")
                return cursor.rowcount == 1
            except Exception:
                if db.in_transaction:
                    db.execute("ROLLBACK")
                raise

    def list_accounts(self, *, owner_id: str, provider_id: str | None = None) -> list[dict]:
        owner = self._identifier(owner_id, "owner_id")
        if provider_id is not None:
            provider_id = self._identifier(provider_id, "provider_id")
        with self._connect() as db:
            if provider_id is None:
                rows = db.execute("""SELECT provider_id,account_id,expires_at,scopes_json,version,connected_at
                    FROM oauth_credentials WHERE owner_id=? ORDER BY provider_id,account_id""", (owner,)).fetchall()
            else:
                rows = db.execute("""SELECT provider_id,account_id,expires_at,scopes_json,version,connected_at
                    FROM oauth_credentials WHERE owner_id=? AND provider_id=? ORDER BY account_id""",
                    (owner, provider_id)).fetchall()
        result = []
        for provider, account, expiry, scopes_json, version, connected in rows:
            try:
                scopes = self._scopes(json.loads(scopes_json))
            except (TypeError, ValueError):
                raise OAuthCredentialError("invalid_stored_scopes") from None
            result.append({"provider_id": provider, "account_id": account, "expires_at": expiry,
                           "scopes": scopes, "version": version, "connected_at": connected})
        return result
