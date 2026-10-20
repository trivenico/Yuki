"""Authenticated encryption for backup archive bytes.

This module encrypts an already-created archive. It deliberately does not create archives,
choose backup contents, or persist keys. Callers must supply a 32-byte key from an external
secret store and must never write the plaintext archive to a temporary file.
"""
from __future__ import annotations

import os
import secrets
import tempfile
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_MAGIC = b"YUKIBAK1"
_NONCE_SIZE = 12
_KEY_SIZE = 32
_DEFAULT_MAX_BYTES = 512 * 1024 * 1024


class BackupEncryptionError(ValueError):
    """Raised when a backup cannot be safely decrypted or validated."""


def _validate_key(key: bytes) -> None:
    if not isinstance(key, bytes) or len(key) != _KEY_SIZE:
        raise BackupEncryptionError("Backup key must be exactly 32 bytes.")


def _read_bounded(path: Path, max_bytes: int) -> bytes:
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes < 1:
        raise BackupEncryptionError("max_bytes must be a positive integer.")
    if path.is_symlink() or not path.is_file():
        raise BackupEncryptionError("Backup input must be a regular, non-symlink file.")
    with path.open("rb") as handle:
        data = handle.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise BackupEncryptionError("Backup exceeds the configured size limit.")
    return data


def _atomic_write(destination: Path, payload: bytes) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() and destination.is_symlink():
        raise BackupEncryptionError("Refusing to replace a symlink destination.")
    fd, temp_name = tempfile.mkstemp(
        prefix=".yuki-backup-", suffix=".tmp", dir=str(destination.parent)
    )
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_path, 0o600)
        os.replace(temp_path, destination)
    except Exception:
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def encrypt_backup(
    source_path: str | Path,
    destination_path: str | Path,
    key: bytes,
    *,
    max_bytes: int = _DEFAULT_MAX_BYTES,
) -> Path:
    """Encrypt an existing archive using AES-256-GCM; only ciphertext is written to disk."""
    _validate_key(key)
    source = Path(source_path).expanduser().resolve(strict=True)
    destination = Path(destination_path).expanduser().absolute()
    if source == destination or destination.exists() and source.samefile(destination):
        raise BackupEncryptionError("Source and destination must be different files.")
    plaintext = _read_bounded(source, max_bytes)
    nonce = secrets.token_bytes(_NONCE_SIZE)
    ciphertext = AESGCM(key).encrypt(nonce, plaintext, _MAGIC)
    _atomic_write(destination, _MAGIC + nonce + ciphertext)
    return destination


def decrypt_backup(
    source_path: str | Path,
    destination_path: str | Path,
    key: bytes,
    *,
    max_bytes: int = _DEFAULT_MAX_BYTES,
) -> Path:
    """Authenticate and decrypt an archive; never write unauthenticated plaintext."""
    _validate_key(key)
    source = Path(source_path).expanduser().resolve(strict=True)
    destination = Path(destination_path).expanduser().absolute()
    if source == destination or destination.exists() and source.samefile(destination):
        raise BackupEncryptionError("Source and destination must be different files.")
    encrypted = _read_bounded(source, max_bytes + len(_MAGIC) + _NONCE_SIZE + 16)
    header_size = len(_MAGIC) + _NONCE_SIZE
    if len(encrypted) < header_size + 16 or not encrypted.startswith(_MAGIC):
        raise BackupEncryptionError("Backup format is invalid or unsupported.")
    nonce = encrypted[len(_MAGIC):header_size]
    try:
        plaintext = AESGCM(key).decrypt(nonce, encrypted[header_size:], _MAGIC)
    except InvalidTag as exc:
        raise BackupEncryptionError("Backup authentication failed; key or data is invalid.") from exc
    if len(plaintext) > max_bytes:
        raise BackupEncryptionError("Decrypted backup exceeds the configured size limit.")
    _atomic_write(destination, plaintext)
    return destination
