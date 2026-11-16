"""Password-encrypted envelope for portable memory backup payloads.

This module encrypts opaque bytes only; snapshot schema and owner authorization
belong to the memory backup service. No plaintext fallback is permitted.
"""
from __future__ import annotations

import base64
import binascii
import json
import secrets
from typing import Any


class MemoryBackupError(ValueError):
    """Stable, non-sensitive memory backup failure."""


_FORMAT = "yuki-memory-backup"
_VERSION = 1
_KDF = "scrypt"
_CIPHER = "AES-256-GCM"
_SCRYPT_N = 1 << 15
_SCRYPT_R = 8
_SCRYPT_P = 1
_SALT_BYTES = 16
_NONCE_BYTES = 12
_TAG_BYTES = 16
_MAX_PLAINTEXT_BYTES = 8 * 1024 * 1024
_MAX_ENVELOPE_BYTES = 12 * 1024 * 1024
_AAD = b"yuki-memory-backup\\x00v1"


def _crypto():
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
    except ImportError:
        raise MemoryBackupError("memory_backup_crypto_unavailable") from None
    return AESGCM, Scrypt


def _password_bytes(password: str | bytes) -> bytes:
    if isinstance(password, str):
        try:
            value = password.encode("utf-8")
        except UnicodeEncodeError:
            raise MemoryBackupError("invalid_backup_password") from None
    elif isinstance(password, bytes):
        value = password
    else:
        raise MemoryBackupError("invalid_backup_password")
    if len(value) < 12 or len(value) > 1024:
        raise MemoryBackupError("invalid_backup_password")
    return value


def _derive(password: bytes, salt: bytes, Scrypt) -> bytes:
    try:
        return Scrypt(
            salt=salt, length=32, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P,
            maxmem=128 * 1024 * 1024,
        ).derive(password)
    except Exception:
        raise MemoryBackupError("memory_backup_key_derivation_failed") from None


def encrypt_backup(plaintext: bytes, password: str | bytes) -> bytes:
    """Return a JSON envelope containing only authenticated ciphertext."""
    if not isinstance(plaintext, bytes) or not 1 <= len(plaintext) <= _MAX_PLAINTEXT_BYTES:
        raise MemoryBackupError("invalid_backup_payload_size")
    password_value = _password_bytes(password)
    AESGCM, Scrypt = _crypto()
    salt = secrets.token_bytes(_SALT_BYTES)
    nonce = secrets.token_bytes(_NONCE_BYTES)
    key = _derive(password_value, salt, Scrypt)
    try:
        ciphertext = AESGCM(key).encrypt(nonce, plaintext, _AAD)
    except Exception:
        raise MemoryBackupError("memory_backup_encryption_failed") from None
    envelope = {
        "format": _FORMAT,
        "version": _VERSION,
        "kdf": {"name": _KDF, "n": _SCRYPT_N, "r": _SCRYPT_R, "p": _SCRYPT_P,
                "salt": base64.b64encode(salt).decode("ascii")},
        "cipher": {"name": _CIPHER, "nonce": base64.b64encode(nonce).decode("ascii")},
        "ciphertext": base64.b64encode(ciphertext).decode("ascii"),
    }
    encoded = json.dumps(envelope, sort_keys=True, separators=(",", ":")).encode("ascii")
    if len(encoded) > _MAX_ENVELOPE_BYTES:
        raise MemoryBackupError("memory_backup_envelope_too_large")
    return encoded


def decrypt_backup(envelope_bytes: bytes, password: str | bytes) -> bytes:
    """Authenticate and decrypt a supported envelope; reject unknown parameters."""
    if not isinstance(envelope_bytes, bytes) or not 1 <= len(envelope_bytes) <= _MAX_ENVELOPE_BYTES:
        raise MemoryBackupError("invalid_memory_backup_envelope")
    password_value = _password_bytes(password)
    try:
        envelope: Any = json.loads(envelope_bytes.decode("ascii"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise MemoryBackupError("invalid_memory_backup_envelope") from None
    if not isinstance(envelope, dict) or set(envelope) != {
        "format", "version", "kdf", "cipher", "ciphertext"
    }:
        raise MemoryBackupError("unsupported_memory_backup_envelope")
    if envelope["format"] != _FORMAT or type(envelope["version"]) is not int or envelope["version"] != _VERSION:
        raise MemoryBackupError("unsupported_memory_backup_envelope")
    kdf = envelope["kdf"]
    cipher = envelope["cipher"]
    if (not isinstance(kdf, dict) or set(kdf) != {"name", "n", "r", "p", "salt"}
            or kdf.get("name") != _KDF or type(kdf.get("n")) is not int
            or type(kdf.get("r")) is not int or type(kdf.get("p")) is not int
            or (kdf["n"], kdf["r"], kdf["p"]) != (_SCRYPT_N, _SCRYPT_R, _SCRYPT_P)):
        raise MemoryBackupError("unsupported_memory_backup_kdf")
    if not isinstance(cipher, dict) or set(cipher) != {"name", "nonce"} or cipher.get("name") != _CIPHER:
        raise MemoryBackupError("unsupported_memory_backup_cipher")
    try:
        salt = base64.b64decode(kdf["salt"], validate=True)
        nonce = base64.b64decode(cipher["nonce"], validate=True)
        ciphertext = base64.b64decode(envelope["ciphertext"], validate=True)
    except (TypeError, ValueError, binascii.Error):
        raise MemoryBackupError("invalid_memory_backup_encoding") from None
    if len(salt) != _SALT_BYTES or len(nonce) != _NONCE_BYTES:
        raise MemoryBackupError("invalid_memory_backup_parameters")
    if not _TAG_BYTES < len(ciphertext) <= _MAX_PLAINTEXT_BYTES + _TAG_BYTES:
        raise MemoryBackupError("invalid_backup_payload_size")
    AESGCM, Scrypt = _crypto()
    key = _derive(password_value, salt, Scrypt)
    try:
        plaintext = AESGCM(key).decrypt(nonce, ciphertext, _AAD)
    except Exception:
        raise MemoryBackupError("memory_backup_authentication_failed") from None
    if not 1 <= len(plaintext) <= _MAX_PLAINTEXT_BYTES:
        raise MemoryBackupError("invalid_backup_payload_size")
    return plaintext
