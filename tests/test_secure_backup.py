import tempfile
import unittest
from pathlib import Path

try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # noqa: F401
except ImportError:
    AESGCM = None

from core.secure_backup import (
    BackupError,
    create_encrypted_backup,
    restore_encrypted_backup,
)


@unittest.skipIf(AESGCM is None, "cryptography backup extra is not installed")
class SecureBackupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.key = b"y" * 32
        self.source = self.root / "private"
        self.source.mkdir()
        (self.source / "memory.txt").write_text("private memory and token-like text", encoding="utf-8")
        (self.source / "nested").mkdir()
        (self.source / "nested" / "note.txt").write_text("nested note", encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def test_round_trip_and_ciphertext_does_not_contain_plaintext(self):
        backup = self.root / "private.yukibak"
        create_encrypted_backup([self.source], backup, self.key)
        data = backup.read_bytes()
        self.assertTrue(data.startswith(b"YUKIBAK1"))
        self.assertNotIn(b"private memory", data)
        destination = self.root / "restored"
        restore_encrypted_backup(backup, destination, self.key)
        self.assertEqual(
            (destination / "private" / "memory.txt").read_text(encoding="utf-8"),
            "private memory and token-like text",
        )
        self.assertEqual(
            (destination / "private" / "nested" / "note.txt").read_text(encoding="utf-8"),
            "nested note",
        )

    def test_wrong_key_and_tampering_fail_closed(self):
        backup = self.root / "private.yukibak"
        create_encrypted_backup([self.source], backup, self.key)
        destination = self.root / "wrong-key-restore"
        with self.assertRaises(BackupError):
            restore_encrypted_backup(backup, destination, b"x" * 32)
        # Authentication fails before the destination is created or touched.
        self.assertFalse(destination.exists())
        corrupted = bytearray(backup.read_bytes())
        corrupted[-1] ^= 0x01
        backup.write_bytes(corrupted)
        with self.assertRaises(BackupError):
            restore_encrypted_backup(backup, self.root / "tampered-restore", self.key)

    def test_restore_refuses_nonempty_destination(self):
        backup = self.root / "private.yukibak"
        create_encrypted_backup([self.source], backup, self.key)
        destination = self.root / "not-empty"
        destination.mkdir()
        (destination / "keep.txt").write_text("keep", encoding="utf-8")
        with self.assertRaises(BackupError):
            restore_encrypted_backup(backup, destination, self.key)
        self.assertEqual((destination / "keep.txt").read_text(encoding="utf-8"), "keep")

    def test_source_size_limit_is_enforced(self):
        with self.assertRaises(BackupError):
            create_encrypted_backup([self.source], self.root / "too-small.yukibak", self.key, max_bytes=2)

    def test_key_length_is_validated(self):
        with self.assertRaises(BackupError):
            create_encrypted_backup([self.source], self.root / "invalid.yukibak", b"short")


if __name__ == "__main__":
    unittest.main()
