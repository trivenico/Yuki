"""OS credential-store backed encryption keys for Yuki voice artifacts.

Only native Windows Credential Manager, macOS Keychain, and Linux
SecretService backends are accepted. The key is never written to app config.
"""
from __future__ import annotations

import base64
import ctypes
import hashlib
import os
import re
import stat
import threading
import time
from contextlib import contextmanager
from typing import Any, Iterator


_SERVICE = "Yuki.VoiceArtifacts.v1"
_ACCOUNT_RE = re.compile(r"^[a-zA-Z0-9_.@+-]{1,200}$")
_KEY_BYTES = 32
_LOCK_TIMEOUT_SECONDS = 30


class VoiceKeyStoreError(RuntimeError):
    """Base error for voice encryption key-store operations."""


class UnsupportedCredentialBackendError(VoiceKeyStoreError):
    """Raised when the active keyring backend is not an approved OS store."""


@contextmanager
def _cross_process_key_lock(account: str, lock_directory: str) -> Iterator[None]:
    """Serialize key lifecycle operations across Yuki processes for this OS user."""
    if os.name == "nt":
        name = "Local\\YukiVoiceKeyLock-" + account
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.argtypes = (ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p)
        kernel32.CreateMutexW.restype = ctypes.c_void_p
        kernel32.WaitForSingleObject.argtypes = (ctypes.c_void_p, ctypes.c_uint)
        kernel32.WaitForSingleObject.restype = ctypes.c_uint
        kernel32.ReleaseMutex.argtypes = (ctypes.c_void_p,)
        kernel32.CloseHandle.argtypes = (ctypes.c_void_p,)
        handle = kernel32.CreateMutexW(None, 0, name)
        if not handle:
            raise VoiceKeyStoreError("Could not create the per-user voice key lock.")
        try:
            result = kernel32.WaitForSingleObject(handle, _LOCK_TIMEOUT_SECONDS * 1000)
            if result not in (0x00000000, 0x00000080):  # WAIT_OBJECT_0 / WAIT_ABANDONED
                raise VoiceKeyStoreError("Timed out waiting for the per-user voice key lock.")
            try:
                yield
            finally:
                if not kernel32.ReleaseMutex(handle):
                    raise VoiceKeyStoreError("Could not release the per-user voice key lock.")
        finally:
            kernel32.CloseHandle(handle)
        return

    import fcntl

    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        directory_fd = os.open(lock_directory, directory_flags)
    except OSError as exc:
        raise VoiceKeyStoreError("The private voice key-lock directory is unavailable.") from exc
    try:
        directory_stat = os.fstat(directory_fd)
        if not stat.S_ISDIR(directory_stat.st_mode):
            raise VoiceKeyStoreError("The voice key-lock path is not a directory.")
        if hasattr(os, "getuid") and directory_stat.st_uid != os.getuid():
            raise VoiceKeyStoreError("The voice key-lock directory is not owned by this user.")
        if directory_stat.st_mode & 0o077:
            raise VoiceKeyStoreError("The voice key-lock directory must be private (mode 0700).")
        flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
        try:
            lock_fd = os.open(".voice-key-" + account + ".lock", flags, 0o600, dir_fd=directory_fd)
        except OSError as exc:
            raise VoiceKeyStoreError("Could not open the per-user voice key lock.") from exc
        try:
            lock_stat = os.fstat(lock_fd)
            if not stat.S_ISREG(lock_stat.st_mode):
                raise VoiceKeyStoreError("The voice key lock is not a regular file.")
            if hasattr(os, "getuid") and lock_stat.st_uid != os.getuid():
                raise VoiceKeyStoreError("The voice key lock is not owned by this user.")
            if lock_stat.st_mode & 0o077:
                os.fchmod(lock_fd, 0o600)
            deadline = time.monotonic() + _LOCK_TIMEOUT_SECONDS
            while True:
                try:
                    fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise VoiceKeyStoreError("Timed out waiting for the per-user voice key lock.")
                    time.sleep(0.05)
            try:
                yield
            finally:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
        finally:
            os.close(lock_fd)
    finally:
        os.close(directory_fd)


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
        with self._lock, _cross_process_key_lock(self._account, self._lock_directory):
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
