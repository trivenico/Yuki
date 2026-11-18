"""OS credential-store backed encryption keys for Yuki voice artifacts.

Only native Windows Credential Manager, macOS Keychain, and Linux
SecretService backends are accepted. The key is never written to app config.
"""
from __future__ import annotations

import base64
import hashlib
import os
import re
import threading
from typing import Any

from voice.process_lock import cross_process_lock


_SERVICE = "Yuki.VoiceArtifacts.v1"
_ACCOUNT_RE = re.compile(r"^[a-zA-Z0-9_.@+-]{1,200}$")
_KEY_BYTES = 32


class VoiceKeyStoreError(RuntimeError):
    """Base error for voice encryption key-store operations."""


class UnsupportedCredentialBackendError(VoiceKeyStoreError):
    """Raised when the active keyring backend is not an approved OS store."""


class VoiceEncryptionKeyProvider:
    """Retrieve or create a user-scoped AES-256 key in an approved OS key store.

    keyring_module is injectable for tests. The selected backend is checked
    before any key operation to reject fail/plaintext/third-party file stores.
    lock_directory must be a private, per-user application data directory.
    """

    def __init__(
        self, user_scope: str, *, lock_directory: str | os.PathLike[str],
        keyring_module: Any | None = None,
    ):
        if not isinstance(user_scope, str) or not user_scope.strip() or len(user_scope) > 1024:
            raise ValueError("A non-empty user scope is required.")
        if not isinstance(lock_directory, (str, os.PathLike)) or not str(lock_directory).strip():
            raise ValueError("A private per-user lock directory is required.")
        self._account = hashlib.sha256(user_scope.strip().encode("utf-8")).hexdigest()
        self._lock_directory = os.path.abspath(os.path.expanduser(str(lock_directory)))
        self._keyring = keyring_module
        self._lock = threading.RLock()

    def _load_keyring(self):
        if self._keyring is None:
            try:
                import keyring
            except ImportError as exc:
                raise VoiceKeyStoreError(
                    "Install Yuki's desktop dependencies to use the OS credential store."
                ) from exc
            self._keyring = keyring
        return self._keyring

    @staticmethod
    def _validate_backend(keyring_module: Any) -> None:
        try:
            backend = keyring_module.get_keyring()
            module = type(backend).__module__
            priority = getattr(backend, "priority", 0)
        except Exception as exc:
            raise VoiceKeyStoreError("The operating-system credential store is unavailable.") from exc
        approved = (
            module.startswith("keyring.backends.Windows")
            or module.startswith("keyring.backends.macOS")
            or module.startswith("keyring.backends.SecretService")
        )
        if not approved or not isinstance(priority, (int, float)) or priority <= 0:
            raise UnsupportedCredentialBackendError(
                "Yuki requires Windows Credential Manager, macOS Keychain, or Linux SecretService. "
                "Plaintext, fail, and unrecognized credential backends are not supported."
            )

    def get_or_create_key(self) -> bytes:
        keyring_module = self._load_keyring()
        self._validate_backend(keyring_module)
        with self._lock, cross_process_lock(self._account, self._lock_directory, namespace="VoiceKey", error_type=VoiceKeyStoreError):
            try:
                encoded = keyring_module.get_password(_SERVICE, self._account)
                if encoded is not None:
                    if not isinstance(encoded, str):
                        raise VoiceKeyStoreError("Stored voice encryption key has an invalid format.")
                    try:
                        key = base64.b64decode(encoded.encode("ascii"), altchars=b"-_", validate=True)
                    except (ValueError, UnicodeError) as exc:
                        raise VoiceKeyStoreError("Stored voice encryption key has an invalid format.") from exc
                    if len(key) != _KEY_BYTES:
                        raise VoiceKeyStoreError("Stored voice encryption key has an invalid length.")
                    return key
                import secrets
                key = secrets.token_bytes(_KEY_BYTES)
                encoded = base64.urlsafe_b64encode(key).decode("ascii")
                keyring_module.set_password(_SERVICE, self._account, encoded)
                persisted = keyring_module.get_password(_SERVICE, self._account)
                if persisted != encoded:
                    raise VoiceKeyStoreError("The OS credential store did not persist the voice key.")
                return key
            except VoiceKeyStoreError:
                raise
            except Exception as exc:
                raise VoiceKeyStoreError("Could not access the voice encryption key in the OS credential store.") from exc

    def delete_key(self, *, confirmed: bool) -> None:
        if confirmed is not True:
            raise VoiceKeyStoreError("Explicit confirmation is required to delete the voice encryption key.")
        keyring_module = self._load_keyring()
        self._validate_backend(keyring_module)
        with self._lock, _cross_process_key_lock(self._account, self._lock_directory):
            try:
                encoded = keyring_module.get_password(_SERVICE, self._account)
                if encoded is None:
                    return
                keyring_module.delete_password(_SERVICE, self._account)
                if keyring_module.get_password(_SERVICE, self._account) is not None:
                    raise VoiceKeyStoreError("The OS credential store did not delete the voice key.")
            except VoiceKeyStoreError:
                raise
            except Exception as exc:
                raise VoiceKeyStoreError("Could not delete the voice encryption key from the OS credential store.") from exc
