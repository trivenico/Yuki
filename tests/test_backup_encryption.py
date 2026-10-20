"""Tests for authenticated backup encryption without external services."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from core.backup_encryption import (
    BackupEncryptionError,
    decrypt_backup,
    encrypt_backup,
)


class BackupEncryptionTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.source = self.root / "private-backup.tar"
        self.source.write_bytes(b"synthetic private backup contents")
        self.key = os.urandom(32)
        self.encrypted = self.root / "backup.yukibak"
        self.restored = self.root / "restored.tar"

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_round_trip_does_not_store_plaintext_in_encrypted_file(self):
        encrypt_backup(self.source, self.encrypted, self.key)
        self.assertNotIn(b"synthetic private backup contents", self.encrypted.read_bytes())
        decrypt_backup(self.encrypted, self.restored, self.key)
        self.assertEqual(self.restored.read_bytes(), self.source.read_bytes())
        self.assertEqual(self.encrypted.stat().st_mode & 0o777, 0o600)

    def test_wrong_key_fails_without_creating_plaintext_destination(self):
        encrypt_backup(self.source, self.encrypted, self.key)
        with self.assertRaisesRegex(BackupEncryptionError, "authentication failed"):
            decrypt_backup(self.encrypted, self.restored, os.urandom(32))
        self.assertFalse(self.restored.exists())

    def test_tampering_fails_authentication(self):
        encrypt_backup(self.source, self.encrypted, self.key)
        data = bytearray(self.encrypted.read_bytes())
        data[-1] ^= 1
        self.encrypted.write_bytes(data)
        with self.assertRaisesRegex(BackupEncryptionError, "authentication failed"):
            decrypt_backup(self.encrypted, self.restored, self.key)
        self.assertFalse(self.restored.exists())

    def test_rejects_invalid_key_and_oversized_input(self):
        with self.assertRaises(BackupEncryptionError):
            encrypt_backup(self.source, self.encrypted, b"short")
        with self.assertRaisesRegex(BackupEncryptionError, "size limit"):
            encrypt_backup(self.source, self.encrypted, self.key, max_bytes=4)
        self.assertFalse(self.encrypted.exists())

    def test_rejects_source_and_destination_alias(self):
        with self.assertRaisesRegex(BackupEncryptionError, "different files"):
            encrypt_backup(self.source, self.source, self.key)

    def test_rejects_symlink_input(self):
        link = self.root / "backup-link.tar"
        link.symlink_to(self.source)
        with self.assertRaisesRegex(BackupEncryptionError, "non-symlink"):
            encrypt_backup(link, self.encrypted, self.key)


if __name__ == "__main__":
    unittest.main()
