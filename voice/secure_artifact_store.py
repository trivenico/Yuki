"""Encrypted local storage for consented voice samples and generated artifacts.

The 256-bit key must be supplied by an OS-keystore adapter (for example,
Keychain, Credential Manager, or Secret Service). This module never persists
or logs the key and never performs network access.
"""
from __future__ import annotations

import os
import re
import secrets
import stat
import tempfile
import threading
import uuid
from pathlib import Path
from typing import Callable

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


_MAGIC = b"YKV1"
_NONCE_BYTES = 12
_KEY_BYTES = 32
_MAX_ARTIFACT_BYTES = 20 * 1024 * 1024
_ARTIFACT_ID = re.compile(r"^[a-f0-9]{32}$")


class VoiceArtifactStoreError(RuntimeError):
    """Base error for encrypted voice artifact storage."""


class VoiceArtifactAuthorizationError(VoiceArtifactStoreError):
    """Raised when the current session is not authorized."""


class EncryptedVoiceArtifactStore:
    """Store small voice artifacts encrypted with a key held outside this store.

    key must come from the operating-system credential store. The store does
    not provide secure-erasure guarantees: SSD snapshots, backups, and filesystem
    journals may retain data after a file is deleted.
    """

    def __init__(
        self,
        directory: str | os.PathLike[str],
        *,
        key: bytes,
        authorize: Callable[[], bool],
        max_artifact_bytes: int = _MAX_ARTIFACT_BYTES,
    ):
        if not isinstance(key, bytes) or len(key) != _KEY_BYTES:
            raise ValueError("An externally managed 256-bit encryption key is required.")
        if not callable(authorize):
            raise TypeError("An authorization callback is required.")
        if isinstance(max_artifact_bytes, bool) or not isinstance(max_artifact_bytes, int):
            raise ValueError("max_artifact_bytes must be an integer.")
        if not 1 <= max_artifact_bytes <= _MAX_ARTIFACT_BYTES:
            raise ValueError("max_artifact_bytes is outside the supported limit.")
        self._cipher = AESGCM(key)
        self._closed = False
        self._authorize = authorize
        self._max_bytes = max_artifact_bytes
        self._lock = threading.RLock()
        self._directory = Path(directory).expanduser()
        if self._directory.exists() and self._directory.is_symlink():
            raise VoiceArtifactStoreError("Voice artifact directory must not be a symlink.")
        self._directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self._directory.is_symlink() or not self._directory.is_dir():
            raise VoiceArtifactStoreError("Voice artifact directory is not a safe directory.")
        try:
            os.chmod(self._directory, 0o700)
            self._directory_fd = None
            self._dir_fd_supported = (
                os.name == "posix"
                and os.open in os.supports_dir_fd
                and os.unlink in os.supports_dir_fd
                and os.link in os.supports_dir_fd
            )
            if self._dir_fd_supported:
                flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
                self._directory_fd = os.open(self._directory, flags)
                directory_stat = os.fstat(self._directory_fd)
                if not stat.S_ISDIR(directory_stat.st_mode):
                    raise VoiceArtifactStoreError("Voice artifact directory is not a directory.")
                if hasattr(os, "getuid") and directory_stat.st_uid != os.getuid():
                    raise VoiceArtifactStoreError("Voice artifact directory is not owned by this user.")
                if directory_stat.st_mode & 0o077:
                    raise VoiceArtifactStoreError("Voice artifact directory must be private (mode 0700).")
        except VoiceArtifactStoreError:
            raise
        except OSError as exc:
            raise VoiceArtifactStoreError("Could not securely open voice artifact directory.") from exc

    def _require_open(self) -> None:
        if self._closed:
            raise VoiceArtifactStoreError("Voice artifact store is closed.")

    def _require_authorized(self) -> None:
        self._require_open()
        try:
            allowed = self._authorize()
        except Exception as exc:
            raise VoiceArtifactAuthorizationError("Voice artifact authorization could not be verified.") from exc
        if allowed is not True:
            raise VoiceArtifactAuthorizationError("An authenticated session is required for voice artifacts.")

    def close(self) -> None:
        """Close the retained directory handle; safe to call more than once."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            if self._directory_fd is not None:
                os.close(self._directory_fd)
                self._directory_fd = None

    @staticmethod
    def _aad(artifact_id: str) -> bytes:
        return b"YukiVoiceArtifact:v1:" + artifact_id.encode("ascii")

    def _path_for(self, artifact_id: str) -> Path:
        if not isinstance(artifact_id, str) or not _ARTIFACT_ID.fullmatch(artifact_id):
            raise ValueError("Invalid voice artifact identifier.")
        return self._directory / (artifact_id + ".ykv")

    def put(self, data: bytes, *, source_owner_consent: bool) -> str:
        self._require_authorized()
        if source_owner_consent is not True:
            raise VoiceArtifactAuthorizationError("Explicit source-owner consent is required before storing voice audio.")
        if not isinstance(data, bytes) or not data:
            raise ValueError("Voice artifact must be non-empty bytes.")
        if len(data) > self._max_bytes:
            raise VoiceArtifactStoreError("Voice artifact exceeds the configured size limit.")
        artifact_id = uuid.uuid4().hex
        nonce = os.urandom(_NONCE_BYTES)
        encrypted = _MAGIC + nonce + self._cipher.encrypt(nonce, data, self._aad(artifact_id))
        destination = self._path_for(artifact_id)
        with self._lock:
            self._require_open()
            if self._dir_fd_supported:
                temporary_name = ".voice-" + secrets.token_hex(16) + ".tmp"
                temporary_fd = None
                try:
                    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
                    temporary_fd = os.open(temporary_name, flags, 0o600, dir_fd=self._directory_fd)
                    with os.fdopen(temporary_fd, "wb") as handle:
                        temporary_fd = None
                        handle.write(encrypted)
                        handle.flush()
                        os.fsync(handle.fileno())
                    try:
                        os.link(
                            temporary_name, artifact_id + ".ykv",
                            src_dir_fd=self._directory_fd, dst_dir_fd=self._directory_fd,
                            follow_symlinks=False,
                        )
                    except FileExistsError as exc:
                        raise VoiceArtifactStoreError("Voice artifact identifier collision.") from exc
                    os.unlink(temporary_name, dir_fd=self._directory_fd)
                    os.fsync(self._directory_fd)
                except VoiceArtifactStoreError:
                    raise
                except OSError as exc:
                    raise VoiceArtifactStoreError("Voice artifact could not be stored safely.") from exc
                finally:
                    if temporary_fd is not None:
                        os.close(temporary_fd)
                    try:
                        os.unlink(temporary_name, dir_fd=self._directory_fd)
                    except OSError:
                        pass
            else:
                fd, temporary = tempfile.mkstemp(prefix=".voice-", suffix=".tmp", dir=self._directory)
                try:
                    try:
                        os.fchmod(fd, 0o600)
                    except (AttributeError, OSError):
                        pass
                    with os.fdopen(fd, "wb") as handle:
                        handle.write(encrypted)
                        handle.flush()
                        os.fsync(handle.fileno())
                    if destination.exists() or destination.is_symlink():
                        raise VoiceArtifactStoreError("Voice artifact identifier collision.")
                    os.replace(temporary, destination)
                except Exception:
                    try:
                        os.close(fd)
                    except OSError:
                        pass
                    raise
                finally:
                    try:
                        if os.path.exists(temporary):
                            os.unlink(temporary)
                    except OSError:
                        pass
        return artifact_id

    def get(self, artifact_id: str) -> bytes:
        self._require_authorized()
        path = self._path_for(artifact_id)
        with self._lock:
            self._require_open()
            flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
            try:
                if self._dir_fd_supported:
                    fd = os.open(artifact_id + ".ykv", flags, dir_fd=self._directory_fd)
                else:
                    if path.is_symlink():
                        raise VoiceArtifactStoreError("Voice artifact must not be a symlink.")
                    fd = os.open(path, flags)
                file_stat = os.fstat(fd)
                if not stat.S_ISREG(file_stat.st_mode):
                    os.close(fd)
                    raise VoiceArtifactStoreError("Voice artifact is not a regular file.")
                if file_stat.st_size > self._max_bytes + len(_MAGIC) + _NONCE_BYTES + 16:
                    os.close(fd)
                    raise VoiceArtifactStoreError("Voice artifact exceeds its size limit.")
                with os.fdopen(fd, "rb") as handle:
                    payload = handle.read(self._max_bytes + len(_MAGIC) + _NONCE_BYTES + 17)
            except VoiceArtifactStoreError:
                raise
            except OSError as exc:
                raise VoiceArtifactStoreError("Voice artifact is unavailable or could not be read.") from exc
        minimum = len(_MAGIC) + _NONCE_BYTES + 16
        if len(payload) < minimum or len(payload) > self._max_bytes + minimum or not payload.startswith(_MAGIC):
            raise VoiceArtifactStoreError("Voice artifact has an invalid format or size.")
        nonce = payload[len(_MAGIC):len(_MAGIC) + _NONCE_BYTES]
        ciphertext = payload[len(_MAGIC) + _NONCE_BYTES:]
        try:
            return self._cipher.decrypt(nonce, ciphertext, self._aad(artifact_id))
        except Exception as exc:
            raise VoiceArtifactStoreError("Voice artifact authentication failed.") from exc

    def delete(self, artifact_id: str, *, confirmed: bool) -> None:
        self._require_authorized()
        if confirmed is not True:
            raise VoiceArtifactAuthorizationError("Explicit confirmation is required to delete voice audio.")
        path = self._path_for(artifact_id)
        with self._lock:
            self._require_open()
            try:
                if self._dir_fd_supported:
                    os.unlink(artifact_id + ".ykv", dir_fd=self._directory_fd)
                    os.fsync(self._directory_fd)
                else:
                    if path.is_symlink():
                        raise VoiceArtifactStoreError("Voice artifact must not be a symlink.")
                    path.unlink()
            except VoiceArtifactStoreError:
                raise
            except OSError as exc:
                raise VoiceArtifactStoreError("Voice artifact could not be deleted safely.") from exc
