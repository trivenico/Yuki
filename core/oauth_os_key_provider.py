"""OS credential-store-backed key lifecycle for OAuth encryption.

The provider uses an explicitly selected native keyring backend. It never uses
keyring's automatic backend selection, environment variables, files, or plaintext
fallbacks. Bootstrap is an explicit one-time operation by the trusted host.
"""
from __future__ import annotations

import base64
import json
import secrets
import sys
import threading
from typing import Protocol


class OAuthOSKeyStoreError(RuntimeError):
    """Stable, non-sensitive OS credential-store failure."""


class PasswordBackend(Protocol):
    def get_password(self, service: str, username: str) -> str | None: ...
    def set_password(self, service: str, username: str, password: str) -> None: ...


class OSKeyringOAuthKeyProvider:
    """AES-GCM key provider backed by Windows Credential Locker, macOS Keychain,
    or Linux Secret Service.

    The backend is selected explicitly for the current OS. A host may inject a
    trusted backend for tests or a separately reviewed native integration.
    Initialization and rotation must be serialized by the owning Yuki process.
    Historical AES keys are retained; deletion is intentionally not implemented.
    """

    _FORMAT = 1
    _KEY_BYTES = 32
    _SERVICE_SUFFIX = ".yuki.oauth"
    _USERNAME_ACTIVE = "aes.active"
    _USERNAME_INDEX = "aes.index"
    _USERNAME_MARKER = "provider.state"
    _USERNAME_HMAC = "hmac.refresh"
    _KEY_PREFIX = "aes.key."
    _LOCK = threading.RLock()

    def __init__(self, *, service_name: str = "yuki", backend: PasswordBackend | None = None):
        if not isinstance(service_name, str) or not service_name or len(service_name) > 80:
            raise ValueError("invalid_service_name")
        self._service = service_name + self._SERVICE_SUFFIX
        self._backend = backend if backend is not None else self._native_backend()

    @staticmethod
    def _native_backend():
        try:
            if sys.platform == "win32":
                from keyring.backends.Windows import WinVaultKeyring
                backend = WinVaultKeyring()
                expected = "keyring.backends.Windows"
            elif sys.platform == "darwin":
                from keyring.backends.macOS import Keyring
                backend = Keyring()
                expected = "keyring.backends.macOS"
            elif sys.platform.startswith("linux"):
                from keyring.backends.SecretService import Keyring
                backend = Keyring()
                expected = "keyring.backends.SecretService"
            else:
                raise OAuthOSKeyStoreError("unsupported_os_credential_store")
            if not backend.__class__.__module__.startswith(expected):
                raise OAuthOSKeyStoreError("unexpected_os_credential_backend")
            priority = backend.priority
            if not isinstance(priority, (int, float)) or priority <= 0:
                raise OAuthOSKeyStoreError("os_credential_store_unavailable")
            return backend
        except OAuthOSKeyStoreError:
            raise
        except Exception:
            raise OAuthOSKeyStoreError("os_credential_store_unavailable") from None

    def _get(self, username: str) -> str | None:
        try:
            return self._backend.get_password(self._service, username)
        except Exception:
            raise OAuthOSKeyStoreError("os_credential_store_read_failed") from None

    def _set(self, username: str, value: str) -> None:
        try:
            self._backend.set_password(self._service, username, value)
        except Exception:
            raise OAuthOSKeyStoreError("os_credential_store_write_failed") from None

    def _require_ready(self) -> None:
        if self._get(self._USERNAME_MARKER) != "ready":
            raise OAuthOSKeyStoreError("os_key_provider_not_initialized")
        if self._get(self._USERNAME_HMAC) is None:
            raise OAuthOSKeyStoreError("os_key_provider_integrity_failure")

    def initialize(self) -> None:
        """Explicit first-run bootstrap; never silently repairs missing key state."""
        with self._LOCK:
            marker = self._get(self._USERNAME_MARKER)
            if marker is not None:
                if marker != "ready":
                    raise OAuthOSKeyStoreError("os_key_provider_initialization_incomplete")
                self._require_ready()
                if self._get(self._USERNAME_ACTIVE) is None:
                    raise OAuthOSKeyStoreError("os_key_provider_integrity_failure")
                return
            if any(self._get(name) is not None for name in (
                self._USERNAME_ACTIVE, self._USERNAME_INDEX, self._USERNAME_HMAC
            )):
                raise OAuthOSKeyStoreError("os_key_provider_uninitialized_state_conflict")
            # Write a tombstone first: an interrupted bootstrap must require operator
            # recovery, never create replacement keys over existing ciphertext.
            self._set(self._USERNAME_MARKER, "initializing")
            key_id = "aesgcm-" + secrets.token_hex(16)
            key = secrets.token_bytes(self._KEY_BYTES)
            self._set(self._KEY_PREFIX + key_id, base64.b64encode(key).decode("ascii"))
            self._set(self._USERNAME_INDEX, json.dumps([key_id], separators=(",", ":")))
            self._set(self._USERNAME_HMAC, base64.b64encode(secrets.token_bytes(32)).decode("ascii"))
            self._set(self._USERNAME_ACTIVE, key_id)
            self._set(self._USERNAME_MARKER, "ready")

    def _key_index(self) -> tuple[str, ...]:
        self._require_ready()
        raw = self._get(self._USERNAME_INDEX)
        try:
            values = json.loads(raw) if isinstance(raw, str) else None
            if (not isinstance(values, list) or not values or len(values) > 128
                    or any(not isinstance(v, str) or not v.startswith("aesgcm-")
                           or len(v) != 39 for v in values)
                    or len(set(values)) != len(values)):
                raise ValueError
            return tuple(values)
        except Exception:
            raise OAuthOSKeyStoreError("os_key_provider_integrity_failure") from None

    def current_key(self) -> tuple[str, bytes]:
        with self._LOCK:
            self._require_ready()
            key_id = self._get(self._USERNAME_ACTIVE)
            if not isinstance(key_id, str) or key_id not in self._key_index():
                raise OAuthOSKeyStoreError("os_key_provider_integrity_failure")
            return key_id, self.get_key(key_id)

    def get_key(self, key_id: str) -> bytes:
        with self._LOCK:
            if not isinstance(key_id, str) or key_id not in self._key_index():
                raise OAuthOSKeyStoreError("historical_key_unavailable")
            raw = self._get(self._KEY_PREFIX + key_id)
            try:
                key = base64.b64decode(raw, validate=True)
                if len(key) != self._KEY_BYTES:
                    raise ValueError
                return key
            except Exception:
                raise OAuthOSKeyStoreError("historical_key_unavailable") from None

    def hmac_key(self) -> bytes:
        """Return the separate stable HMAC key; never reuse AES key material."""
        with self._LOCK:
            self._require_ready()
            raw = self._get(self._USERNAME_HMAC)
            try:
                key = base64.b64decode(raw, validate=True)
                if len(key) != 32:
                    raise ValueError
                return key
            except Exception:
                raise OAuthOSKeyStoreError("os_key_provider_integrity_failure") from None

    def rotate(self) -> str:
        """Create a new active AES key while retaining all historical decrypt keys."""
        with self._LOCK:
            index = list(self._key_index())
            if len(index) >= 128:
                raise OAuthOSKeyStoreError("historical_key_capacity_reached")
            key_id = "aesgcm-" + secrets.token_hex(16)
            key = secrets.token_bytes(self._KEY_BYTES)
            self._set(self._KEY_PREFIX + key_id, base64.b64encode(key).decode("ascii"))
            # Publish index before active pointer; a partial failure leaves old active key.
            self._set(self._USERNAME_INDEX, json.dumps(index + [key_id], separators=(",", ":")))
            self._set(self._USERNAME_ACTIVE, key_id)
            return key_id

    def key_ids(self) -> tuple[str, ...]:
        """Metadata-only inventory; does not expose key bytes."""
        with self._LOCK:
            return self._key_index()
