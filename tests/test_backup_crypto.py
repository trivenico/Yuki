import os
from pathlib import Path
import stat
import tempfile
import unittest

from scripts.backup_crypto import (
    MAGIC,
    BackupCryptoError,
    create_key_file,
    decrypt_file,
    encrypt_file,
    is_encrypted_file,
)


class BackupCryptoTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.key_dir = self.root / "keys"
        self.backup_dir = self.root / "backups"
        self.key_dir.mkdir()
        self.backup_dir.mkdir()
        self.key = create_key_file(self.key_dir / "yuki-backup.key")
        self.source = self.root / "workspace.tar.gz"
        self.source.write_bytes((b"Yuki workspace data\x00" * 8192) + b"end")
        self.encrypted = self.backup_dir / "workspace.yukibak"

    def tearDown(self):
        self.temp.cleanup()

    def test_key_generation_is_random_private_and_never_overwrites(self):
        raw = self.key.read_bytes()
        self.assertEqual(len(raw), 32)
        if os.name != "nt":
            self.assertEqual(stat.S_IMODE(self.key.stat().st_mode), 0o600)
        with self.assertRaises(BackupCryptoError):
            create_key_file(self.key)
        other = create_key_file(self.key_dir / "other.key")
        self.assertNotEqual(raw, other.read_bytes())

    def test_encrypt_and_decrypt_round_trip(self):
        encrypted_result = encrypt_file(self.source, self.encrypted, self.key)
        self.assertTrue(is_encrypted_file(self.encrypted))
        self.assertEqual(self.encrypted.read_bytes()[:len(MAGIC)], MAGIC)
        self.assertEqual(encrypted_result["plaintext_bytes"], self.source.stat().st_size)
        restored = self.root / "restored.tar.gz"
        decrypted_result = decrypt_file(self.encrypted, restored, self.key)
        self.assertEqual(restored.read_bytes(), self.source.read_bytes())
        self.assertEqual(decrypted_result["plaintext_bytes"], self.source.stat().st_size)
        self.assertFalse(list(self.backup_dir.glob(".yuki-*tmp")))

    def test_wrong_key_never_publishes_plaintext(self):
        wrong_key = create_key_file(self.key_dir / "wrong.key")
        encrypt_file(self.source, self.encrypted, self.key)
        restored = self.root / "wrong-key-output.tar.gz"
        with self.assertRaisesRegex(BackupCryptoError, "authentication_failed"):
            decrypt_file(self.encrypted, restored, wrong_key)
        self.assertFalse(restored.exists())
        self.assertEqual(list(self.root.glob(".yuki-decrypt-*.tmp")), [])

    def test_tampering_is_detected_before_publication(self):
        encrypt_file(self.source, self.encrypted, self.key)
        raw = bytearray(self.encrypted.read_bytes())
        raw[len(MAGIC) + 12 + 7] ^= 0x40
        self.encrypted.write_bytes(raw)
        restored = self.root / "tampered-output.tar.gz"
        with self.assertRaisesRegex(BackupCryptoError, "authentication_failed"):
            decrypt_file(self.encrypted, restored, self.key)
        self.assertFalse(restored.exists())
        self.assertEqual(list(self.root.glob(".yuki-decrypt-*.tmp")), [])

    def test_truncated_and_invalid_headers_are_rejected(self):
        invalid = self.backup_dir / "invalid.yukibak"
        invalid.write_bytes(b"not-a-yuki-backup")
        with self.assertRaises(BackupCryptoError):
            decrypt_file(invalid, self.root / "invalid-out", self.key)
        encrypt_file(self.source, self.encrypted, self.key)
        self.encrypted.write_bytes(self.encrypted.read_bytes()[:-8])
        with self.assertRaises(BackupCryptoError):
            decrypt_file(self.encrypted, self.root / "truncated-out", self.key)
        self.assertFalse((self.root / "truncated-out").exists())

    def test_existing_destination_is_never_overwritten(self):
        encrypt_file(self.source, self.encrypted, self.key)
        existing = self.root / "existing.bin"
        existing.write_bytes(b"preserve")
        with self.assertRaisesRegex(BackupCryptoError, "already_exists"):
            decrypt_file(self.encrypted, existing, self.key)
        self.assertEqual(existing.read_bytes(), b"preserve")
        with self.assertRaisesRegex(BackupCryptoError, "already_exists"):
            encrypt_file(self.source, self.encrypted, self.key)

    def test_key_inside_backup_directory_is_rejected(self):
        nested_key = self.backup_dir / "nested.key"
        nested_key.write_bytes(self.key.read_bytes())
        if os.name != "nt":
            nested_key.chmod(0o600)
        with self.assertRaisesRegex(BackupCryptoError, "outside_backup_directory"):
            encrypt_file(self.source, self.backup_dir / "nested.yukibak", nested_key)

    def test_insecure_key_permissions_are_rejected(self):
        if os.name == "nt":
            self.skipTest("POSIX permission bits are not enforced on Windows")
        self.key.chmod(0o644)
        with self.assertRaisesRegex(BackupCryptoError, "permissions_too_open"):
            encrypt_file(self.source, self.encrypted, self.key)

    def test_empty_source_is_rejected(self):
        empty = self.root / "empty.tar.gz"
        empty.touch()
        with self.assertRaisesRegex(BackupCryptoError, "size_invalid"):
            encrypt_file(empty, self.encrypted, self.key)

    def test_encrypted_output_does_not_contain_plaintext_marker(self):
        marker = b"PRIVATE-YUKI-MARKER-DO-NOT-LEAK"
        self.source.write_bytes(marker * 100)
        encrypt_file(self.source, self.encrypted, self.key)
        self.assertNotIn(marker, self.encrypted.read_bytes())


if __name__ == "__main__":
    unittest.main()
