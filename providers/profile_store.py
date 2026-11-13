"""Persistent, owner-scoped provider profiles with OS-vault-only credentials.

Provider metadata is stored in SQLite. Credential material is written only to an
injected credential vault (the default adapter uses the operating-system keyring).
No method returns credentials from list/get metadata operations.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import secrets
import sqlite3
from contextlib import closing
from typing import Protocol

from core.sqlite_file_security import prepare_private_sqlite_path
from urllib.parse import urlsplit


_OWNER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/-]{0,255}$")
_NAME = re.compile(r"^[a-z][a-z0-9_-]{1,47}$")
_ENV_REF = re.compile(r"^[A-Z][A-Z0-9_]{0,127}$")
_SERVICE = "yuki.provider-profile.v1"


class CredentialVault(Protocol):
    def set_secret(self, reference: str, secret: str) -> None: ...
    def get_secret(self, reference: str) -> str | None: ...
    def delete_secret(self, reference: str) -> None: ...


class OSKeyringVault:
    """Use an installed OS-backed keyring; reject fail/in-memory backends."""

    def __init__(self, *, keyring_module=None) -> None:
        if keyring_module is None:
            try:
                import keyring as keyring_module
            except ImportError as exc:
                raise RuntimeError("OS credential vault is unavailable") from exc
        self._keyring = keyring_module
        try:
            backend = keyring_module.get_keyring()
            priority = backend.priority
            backend_identity = (backend.__class__.__module__, backend.__class__.__name__)
        except Exception as exc:
            raise RuntimeError("OS credential vault is unavailable") from exc
        # A positive keyring priority is not proof of secure storage: third-party
        # backends can store credentials in plaintext files. Allow only reviewed
        # native OS vault implementations and fail closed for every other backend.
        secure_native_backends = {
            ("keyring.backends.Windows", "WinVaultKeyring"),
            ("keyring.backends.macOS", "Keyring"),
            ("keyring.backends.SecretService", "Keyring"),
            ("keyring.backends.kwallet", "DBusKeyring"),
            ("keyring.backends.kwallet", "KWallet4"),
        }
        if (not isinstance(priority, (int, float)) or priority <= 0
                or backend_identity not in secure_native_backends):
            raise RuntimeError("No reviewed native OS credential vault backend is available")

    def set_secret(self, reference: str, secret: str) -> None:
        self._keyring.set_password(_SERVICE, reference, secret)

    def get_secret(self, reference: str) -> str | None:
        return self._keyring.get_password(_SERVICE, reference)

    def delete_secret(self, reference: str) -> None:
        try:
            self._keyring.delete_password(_SERVICE, reference)
        except self._keyring.errors.PasswordDeleteError:
            return


@dataclass(frozen=True)
class ProviderProfile:
    owner_id: str
    name: str
    base_url: str
    model: str
    allowed_host: str
    credential_configured: bool
    created_at: float
    updated_at: float


class ProviderProfileStore:
    """SQLite metadata + separate OS-vault credentials, scoped by owner."""

    def __init__(self, database_path: str | Path, vault: CredentialVault) -> None:
        if not all(callable(getattr(vault, method, None)) for method in
                   ("set_secret", "get_secret", "delete_secret")):
            raise TypeError("vault must implement the credential vault contract")
        self.path = Path(prepare_private_sqlite_path(database_path))
        self.vault = vault
        self._closed = False
        with closing(self._connect()) as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS provider_profiles (
                    owner_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    base_url TEXT NOT NULL,
                    model TEXT NOT NULL,
                    allowed_host TEXT NOT NULL,
                    credential_ref TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    PRIMARY KEY(owner_id, name)
                );
                CREATE INDEX IF NOT EXISTS idx_provider_profiles_owner
                    ON provider_profiles(owner_id, name);
                CREATE TABLE IF NOT EXISTS provider_profile_selection (
                    owner_id TEXT PRIMARY KEY,
                    profile_name TEXT NOT NULL,
                    updated_at REAL NOT NULL,
                    FOREIGN KEY(owner_id, profile_name)
                        REFERENCES provider_profiles(owner_id, name) ON DELETE CASCADE
                );
            """)

    def _connect(self):
        if self._closed:
            raise RuntimeError("provider profile store is closed")
        self.path = Path(prepare_private_sqlite_path(self.path))
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=10000")
        db.execute("PRAGMA foreign_keys=ON")
        return db

    @staticmethod
    def _validate_owner(owner_id: str) -> str:
        if not isinstance(owner_id, str) or not _OWNER.fullmatch(owner_id):
            raise ValueError("owner_id is invalid")
        return owner_id

    @staticmethod
    def _validate_name(name: str) -> str:
        if not isinstance(name, str) or not _NAME.fullmatch(name):
            raise ValueError("profile name must be lowercase identifier text")
        return name

    @staticmethod
    def _validate_endpoint(base_url: str, allowed_host: str) -> tuple[str, str]:
        if not isinstance(base_url, str) or len(base_url) > 2048:
            raise ValueError("base_url must be bounded HTTPS URL text")
        if not isinstance(allowed_host, str) or not allowed_host:
            raise ValueError("an explicitly approved exact hostname is required")
        parsed = urlsplit(base_url)
        host = (parsed.hostname or "").lower().rstrip(".")
        approved = allowed_host.lower().rstrip(".")
        if (parsed.scheme != "https" or not host or host != approved
                or parsed.username is not None or parsed.password is not None
                or parsed.query or parsed.fragment or parsed.port not in (None, 443)):
            raise ValueError("endpoint must use HTTPS on the exact approved hostname and default port")
        if not parsed.path.startswith("/") or "\x00" in parsed.path:
            raise ValueError("endpoint path is invalid")
        normalized = f"https://{host}{parsed.path.rstrip('/')}"
        return normalized, host

    def save(self, *, owner_id: str, name: str, base_url: str, model: str,
             allowed_host: str, api_key: str) -> ProviderProfile:
        """Create or rotate a profile; API key is accepted only for vault insertion."""
        owner = self._validate_owner(owner_id)
        profile_name = self._validate_name(name)
        endpoint, host = self._validate_endpoint(base_url, allowed_host)
        if not isinstance(model, str) or not model.strip() or len(model) > 120 or "\x00" in model:
            raise ValueError("model must be bounded non-empty text")
        if not isinstance(api_key, str) or not api_key or len(api_key) > 8192 or "\x00" in api_key:
            raise ValueError("credential must be bounded non-empty text")
        import time
        now = time.time()
        reference = secrets.token_urlsafe(32)
        self.vault.set_secret(reference, api_key)
        old_reference = None
        try:
            with closing(self._connect()) as db:
                db.execute("BEGIN IMMEDIATE")
                previous = db.execute(
                    "SELECT credential_ref, created_at FROM provider_profiles WHERE owner_id=? AND name=?",
                    (owner, profile_name),
                ).fetchone()
                old_reference = previous["credential_ref"] if previous else None
                created = previous["created_at"] if previous else now
                db.execute("""
                    INSERT INTO provider_profiles
                        (owner_id,name,base_url,model,allowed_host,credential_ref,created_at,updated_at)
                    VALUES(?,?,?,?,?,?,?,?)
                    ON CONFLICT(owner_id,name) DO UPDATE SET
                        base_url=excluded.base_url, model=excluded.model,
                        allowed_host=excluded.allowed_host,
                        credential_ref=excluded.credential_ref, updated_at=excluded.updated_at
                """, (owner, profile_name, endpoint, model.strip(), host, reference, created, now))
                db.commit()
        except Exception:
            try:
                self.vault.delete_secret(reference)
            except Exception:
                pass
            raise
        if old_reference:
            try:
                self.vault.delete_secret(old_reference)
            except Exception:
                # New reference is committed and usable. Orphan cleanup is safe to retry.
                pass
        return ProviderProfile(owner, profile_name, endpoint, model.strip(), host, True, created, now)

    def list_profiles(self, owner_id: str) -> tuple[ProviderProfile, ...]:
        owner = self._validate_owner(owner_id)
        with closing(self._connect()) as db:
            rows = db.execute("""
                SELECT owner_id,name,base_url,model,allowed_host,created_at,updated_at
                FROM provider_profiles WHERE owner_id=? ORDER BY name
            """, (owner,)).fetchall()
        profiles = []
        for row in rows:
            try:
                secret = self.vault.get_secret(row["credential_ref"])
                configured = isinstance(secret, str) and bool(secret)
            except Exception:
                configured = False
            profiles.append(ProviderProfile(
                row["owner_id"], row["name"], row["base_url"], row["model"],
                row["allowed_host"], configured, row["created_at"], row["updated_at"],
            ))
        return tuple(profiles)

    def credential_available(self, owner_id: str, name: str) -> bool:
        """Return credential presence without exposing secret material."""
        owner = self._validate_owner(owner_id)
        profile_name = self._validate_name(name)
        with closing(self._connect()) as db:
            row = db.execute(
                "SELECT credential_ref FROM provider_profiles WHERE owner_id=? AND name=?",
                (owner, profile_name),
            ).fetchone()
        if row is None:
            return False
        try:
            secret = self.vault.get_secret(row["credential_ref"])
            return isinstance(secret, str) and bool(secret)
        except Exception:
            return False

    def resolve_for_runtime(self, owner_id: str, name: str) -> dict[str, str]:
        """Internal runtime-only credential resolution; never use for API responses."""
        owner = self._validate_owner(owner_id)
        profile_name = self._validate_name(name)
        with closing(self._connect()) as db:
            row = db.execute("""
                SELECT base_url,model,allowed_host,credential_ref
                FROM provider_profiles WHERE owner_id=? AND name=?
            """, (owner, profile_name)).fetchone()
        if row is None:
            raise KeyError("provider profile not found")
        secret = self.vault.get_secret(row["credential_ref"])
        if not isinstance(secret, str) or not secret:
            raise RuntimeError("provider credential is unavailable in the OS vault")
        return {"base_url": row["base_url"], "model": row["model"],
                "allowed_host": row["allowed_host"], "api_key": secret}

    def select_profile(self, owner_id: str, name: str) -> None:
        """Persist the selected profile for one local/remote owner."""
        owner = self._validate_owner(owner_id)
        profile_name = self._validate_name(name)
        import time
        with closing(self._connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            exists = db.execute(
                "SELECT 1 FROM provider_profiles WHERE owner_id=? AND name=?",
                (owner, profile_name),
            ).fetchone()
            if exists is None:
                db.rollback()
                raise KeyError("provider profile not found")
            db.execute("""
                INSERT INTO provider_profile_selection(owner_id,profile_name,updated_at)
                VALUES(?,?,?)
                ON CONFLICT(owner_id) DO UPDATE SET
                    profile_name=excluded.profile_name, updated_at=excluded.updated_at
            """, (owner, profile_name, time.time()))
            db.commit()

    def selected_profile(self, owner_id: str) -> str | None:
        owner = self._validate_owner(owner_id)
        with closing(self._connect()) as db:
            row = db.execute(
                """SELECT s.profile_name FROM provider_profile_selection s
                   JOIN provider_profiles p
                     ON p.owner_id=s.owner_id AND p.name=s.profile_name
                   WHERE s.owner_id=?""",
                (owner,),
            ).fetchone()
        return None if row is None else row["profile_name"]

    def delete(self, owner_id: str, name: str) -> bool:
        owner = self._validate_owner(owner_id)
        profile_name = self._validate_name(name)
        with closing(self._connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT credential_ref FROM provider_profiles WHERE owner_id=? AND name=?",
                (owner, profile_name),
            ).fetchone()
            if row is None:
                db.rollback()
                return False
            # Delete the vault entry before committing metadata removal. If the
            # OS vault is locked/unavailable, rollback and preserve the profile.
            try:
                secret = self.vault.get_secret(row["credential_ref"])
                if secret:
                    self.vault.delete_secret(row["credential_ref"])
            except Exception as exc:
                db.rollback()
                raise RuntimeError("OS credential vault is unavailable; profile was not removed") from exc
            db.execute("DELETE FROM provider_profile_selection WHERE owner_id=? AND profile_name=?",
                       (owner, profile_name))
            db.execute("DELETE FROM provider_profiles WHERE owner_id=? AND name=?",
                       (owner, profile_name))
            db.commit()
        return True

    def close(self) -> None:
        self._closed = True
