"""One-time, owner-bound OAuth authorization transactions with PKCE S256.

OAuth secrets are encrypted by an injected application vault. This module never
implements token exchange or stores access/refresh tokens.
"""
from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


class OAuthTransactionError(PermissionError):
    """Generic OAuth transaction failure; does not disclose transaction secrets."""


_STATE_RE = re.compile(r"^[A-Za-z0-9_-]{43}$")


def _now():
    return datetime.now(timezone.utc)


def _iso(value):
    return value.astimezone(timezone.utc).isoformat()


def _b64url(value):
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _https_url(value, *, name, allow_query=False):
    if not isinstance(value, str) or not value or len(value) > 2048:
        raise ValueError(f"{name} must be a bounded HTTPS URL.")
    parts = urlsplit(value)
    try:
        port = parts.port
    except ValueError as exc:
        raise ValueError(f"{name} is invalid.") from exc
    if (parts.scheme != "https" or not parts.hostname or parts.username or parts.password
            or port not in (None, 443) or parts.fragment or (not allow_query and parts.query)):
        raise ValueError(f"{name} must be HTTPS on the default port without userinfo or fragments.")
    return urlunsplit((parts.scheme, parts.netloc, parts.path or "/", parts.query, ""))


class OAuthPKCETransactionStore:
    """Short-lived, single-use OAuth state and encrypted PKCE verifier storage."""

    MAX_PENDING_PER_OWNER = 32

    def __init__(self, database_path, *, encrypt, decrypt, allowed_authorization_hosts,
                 ttl_seconds=600):
        if not isinstance(database_path, str) or not database_path.strip():
            raise ValueError("database_path must be a non-empty path.")
        if not callable(encrypt) or not callable(decrypt):
            raise TypeError("OAuth verifier encryption and decryption providers are required.")
        if (not isinstance(allowed_authorization_hosts, (list, tuple, set, frozenset))
                or not allowed_authorization_hosts):
            raise ValueError("An explicit OAuth authorization host allow-list is required.")
        hosts = set()
        for host in allowed_authorization_hosts:
            if not isinstance(host, str) or not host or host != host.lower() or "/" in host or ":" in host:
                raise ValueError("OAuth authorization hosts must be normalized hostnames.")
            hosts.add(host.rstrip("."))
        if type(ttl_seconds) is not int or not 60 <= ttl_seconds <= 1800:
            raise ValueError("ttl_seconds must be between 60 and 1800.")
        self.database_path = str(Path(database_path).expanduser())
        self.encrypt = encrypt
        self.decrypt = decrypt
        self.allowed_authorization_hosts = frozenset(hosts)
        self.ttl_seconds = ttl_seconds
        path = Path(self.database_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.is_symlink():
            raise ValueError("OAuth transaction database cannot be a symlink.")
        with self._connect() as db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS oauth_pkce_transactions (
                    state_hash TEXT PRIMARY KEY,
                    owner_id TEXT NOT NULL,
                    provider_id TEXT NOT NULL,
                    issuer TEXT NOT NULL,
                    client_id TEXT NOT NULL,
                    redirect_uri TEXT NOT NULL,
                    scopes_json TEXT NOT NULL,
                    verifier_ciphertext BLOB NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL
                )
            """)
            db.execute("CREATE INDEX IF NOT EXISTS idx_oauth_pkce_expiry ON oauth_pkce_transactions(expires_at)")
        if path.is_symlink():
            raise ValueError("OAuth transaction database path is unsafe.")
        try:
            path.chmod(0o600)
        except OSError:
            if os.name != "nt":
                raise

    def _connect(self):
        db = sqlite3.connect(self.database_path, timeout=5.0, isolation_level=None)
        db.execute("PRAGMA busy_timeout=5000")
        db.execute("PRAGMA foreign_keys=ON")
        return db

    @staticmethod
    def _validate_identity(owner_id, provider_id, client_id):
        for name, value, limit in (("owner_id", owner_id, 256), ("provider_id", provider_id, 128),
                                   ("client_id", client_id, 512)):
            if not isinstance(value, str) or not value.strip() or len(value) > limit:
                raise ValueError(f"{name} must be a non-empty bounded string.")

    def create(self, *, owner_id, provider_id, issuer, authorization_endpoint,
               client_id, redirect_uri, scopes):
        self._validate_identity(owner_id, provider_id, client_id)
        issuer = _https_url(issuer, name="issuer")
        endpoint = _https_url(authorization_endpoint, name="authorization_endpoint", allow_query=True)
        endpoint_host = (urlsplit(endpoint).hostname or "").lower().rstrip(".")
        if endpoint_host not in self.allowed_authorization_hosts:
            raise ValueError("OAuth authorization endpoint is not on the configured host allow-list.")
        redirect_uri = _https_url(redirect_uri, name="redirect_uri")
        if not isinstance(scopes, (list, tuple)) or not 1 <= len(scopes) <= 32:
            raise ValueError("scopes must contain between 1 and 32 entries.")
        if any(not isinstance(scope, str) or not scope or len(scope) > 128
               or any(ord(ch) < 0x21 or ord(ch) > 0x7e for ch in scope) for scope in scopes):
            raise ValueError("OAuth scopes must be bounded printable ASCII strings.")
        if len(set(scopes)) != len(scopes):
            raise ValueError("OAuth scopes must be unique.")
        state = _b64url(secrets.token_bytes(32))
        verifier = _b64url(secrets.token_bytes(32))
        challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
        state_hash = hashlib.sha256(state.encode("ascii")).hexdigest()
        associated_data = f"{owner_id}\0{provider_id}\0{state_hash}".encode("utf-8")
        try:
            ciphertext = self.encrypt(verifier.encode("ascii"), associated_data)
        except Exception:
            raise OAuthTransactionError("OAuth verifier could not be protected by the configured vault.") from None
        if not isinstance(ciphertext, (bytes, bytearray)) or not ciphertext:
            raise OAuthTransactionError("OAuth verifier could not be protected by the configured vault.")
        now = _now()
        expires = now + timedelta(seconds=self.ttl_seconds)
        db = self._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM oauth_pkce_transactions WHERE expires_at <= ?", (_iso(now),))
            pending = db.execute("SELECT COUNT(*) FROM oauth_pkce_transactions WHERE owner_id=?",
                                  (owner_id,)).fetchone()[0]
            if pending >= self.MAX_PENDING_PER_OWNER:
                raise OAuthTransactionError("Too many pending OAuth authorizations.")
            query_items = parse_qsl(urlsplit(endpoint).query, keep_blank_values=True)
            required = {
                "response_type": "code", "client_id": client_id, "redirect_uri": redirect_uri,
                "scope": " ".join(scopes), "state": state, "code_challenge": challenge,
                "code_challenge_method": "S256",
            }
            if any(key in required for key, _ in query_items):
                raise ValueError("Authorization endpoint contains reserved OAuth parameters.")
            db.execute("""
                INSERT INTO oauth_pkce_transactions
                (state_hash, owner_id, provider_id, issuer, client_id, redirect_uri,
                 scopes_json, verifier_ciphertext, created_at, expires_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (state_hash, owner_id, provider_id, issuer, client_id, redirect_uri,
                  json.dumps(list(scopes)), bytes(ciphertext), _iso(now), _iso(expires)))
            db.execute("COMMIT")
        except Exception:
            if db.in_transaction:
                db.execute("ROLLBACK")
            raise
        finally:
            db.close()
        parts = urlsplit(endpoint)
        query_items.extend(required.items())
        authorization_url = urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query_items), ""))
        return {"authorization_url": authorization_url, "state": state, "expires_at": _iso(expires)}

    def consume(self, *, state, owner_id, provider_id, issuer):
        """Atomically consume a callback transaction; any mismatch fails closed."""
        if (not isinstance(state, str) or not _STATE_RE.fullmatch(state)
                or not isinstance(owner_id, str) or not owner_id
                or not isinstance(provider_id, str) or not provider_id):
            raise OAuthTransactionError("OAuth callback does not match a pending authorization.")
        try:
            issuer = _https_url(issuer, name="issuer")
        except ValueError:
            raise OAuthTransactionError("OAuth callback does not match a pending authorization.") from None
        state_hash = hashlib.sha256(state.encode("ascii")).hexdigest()
        db = self._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("""
                SELECT owner_id, provider_id, issuer, client_id, redirect_uri,
                       scopes_json, verifier_ciphertext, expires_at
                FROM oauth_pkce_transactions WHERE state_hash=?
            """, (state_hash,)).fetchone()
            if row is None:
                raise OAuthTransactionError("OAuth callback does not match a pending authorization.")
            stored_owner, stored_provider, stored_issuer, client_id, redirect_uri, scopes_json, ciphertext, expires_at = row
            valid = (
                hmac.compare_digest(stored_owner, owner_id)
                and hmac.compare_digest(stored_provider, provider_id)
                and hmac.compare_digest(stored_issuer, issuer)
                and expires_at > _iso(_now())
            )
            if not valid:
                db.execute("DELETE FROM oauth_pkce_transactions WHERE state_hash=?", (state_hash,))
                db.execute("COMMIT")
                raise OAuthTransactionError("OAuth callback does not match a pending authorization.")
            associated_data = f"{stored_owner}\0{stored_provider}\0{state_hash}".encode("utf-8")
            try:
                verifier_bytes = self.decrypt(ciphertext, associated_data)
                verifier = verifier_bytes.decode("ascii")
                if not 43 <= len(verifier) <= 128 or not re.fullmatch(r"[A-Za-z0-9._~-]+", verifier):
                    raise ValueError("invalid verifier")
            except Exception:
                db.execute("DELETE FROM oauth_pkce_transactions WHERE state_hash=?", (state_hash,))
                db.execute("COMMIT")
                raise OAuthTransactionError("OAuth transaction could not be securely recovered.") from None
            db.execute("DELETE FROM oauth_pkce_transactions WHERE state_hash=?", (state_hash,))
            db.execute("COMMIT")
            return {"client_id": client_id, "redirect_uri": redirect_uri,
                    "scopes": json.loads(scopes_json), "code_verifier": verifier}
        except Exception:
            if db.in_transaction:
                db.execute("ROLLBACK")
            raise
        finally:
            db.close()
