"""Tests for the encrypted memory backup envelope."""
import json

import pytest

from core.memory_backup_envelope import (
    MemoryBackupError,
    decrypt_backup,
    encrypt_backup,
)


def _crypto_available():
    pytest.importorskip("cryptography")


def test_round_trip_encrypts_payload_and_uses_fresh_salt_and_nonce():
    _crypto_available()
    payload = b'{"schema_version":1,"memories":[]}'
    first = encrypt_backup(payload, "a sufficiently long backup passphrase")
    second = encrypt_backup(payload, "a sufficiently long backup passphrase")
    assert first != second
    assert payload not in first
    assert decrypt_backup(first, "a sufficiently long backup passphrase") == payload


def test_wrong_password_and_tampering_fail_closed():
    _crypto_available()
    envelope = encrypt_backup(b"private memory export", "a sufficiently long backup passphrase")
    with pytest.raises(MemoryBackupError, match="authentication_failed"):
        decrypt_backup(envelope, "a different sufficiently long passphrase")
    tampered = bytearray(envelope)
    tampered[-8] = ord("A") if tampered[-8] != ord("A") else ord("B")
    with pytest.raises(MemoryBackupError):
        decrypt_backup(bytes(tampered), "a sufficiently long backup passphrase")


def test_rejects_unknown_format_and_unsafe_kdf_parameters():
    _crypto_available()
    envelope = json.loads(encrypt_backup(b"payload", "a sufficiently long backup passphrase"))
    envelope["kdf"]["n"] = 1 << 20
    with pytest.raises(MemoryBackupError, match="unsupported_memory_backup_kdf"):
        decrypt_backup(json.dumps(envelope).encode(), "a sufficiently long backup passphrase")


@pytest.mark.parametrize("password", ["short", b"short", "", None])
def test_rejects_weak_or_invalid_password(password):
    with pytest.raises(MemoryBackupError, match="invalid_backup_password"):
        encrypt_backup(b"payload", password)


def test_rejects_empty_and_oversized_payloads():
    with pytest.raises(MemoryBackupError, match="invalid_backup_payload_size"):
        encrypt_backup(b"", "a sufficiently long backup passphrase")
    with pytest.raises(MemoryBackupError, match="invalid_backup_payload_size"):
        encrypt_backup(b"x" * (8 * 1024 * 1024 + 1), "a sufficiently long backup passphrase")
