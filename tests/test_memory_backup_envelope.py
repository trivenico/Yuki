"""Tests for the encrypted memory backup envelope."""
import json
import unittest

from core.memory_backup_envelope import MemoryBackupError, decrypt_backup, encrypt_backup


class MemoryBackupEnvelopeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            import cryptography  # noqa: F401
        except ImportError:
            raise unittest.SkipTest("cryptography is not installed")

    def test_round_trip_encrypts_payload_and_uses_fresh_salt_and_nonce(self):
        payload = b'{"schema_version":1,"memories":[]}'
        first = encrypt_backup(payload, "a sufficiently long backup passphrase")
        second = encrypt_backup(payload, "a sufficiently long backup passphrase")
        self.assertNotEqual(first, second)
        self.assertNotIn(payload, first)
        self.assertEqual(
            decrypt_backup(first, "a sufficiently long backup passphrase"), payload,
        )

    def test_wrong_password_and_tampering_fail_closed(self):
        envelope = encrypt_backup(
            b"private memory export", "a sufficiently long backup passphrase",
        )
        with self.assertRaisesRegex(MemoryBackupError, "authentication_failed"):
            decrypt_backup(envelope, "a different sufficiently long passphrase")
        tampered = bytearray(envelope)
        tampered[-8] = ord("A") if tampered[-8] != ord("A") else ord("B")
        with self.assertRaises(MemoryBackupError):
            decrypt_backup(bytes(tampered), "a sufficiently long backup passphrase")

    def test_rejects_unknown_format_and_unsafe_kdf_parameters(self):
        envelope = json.loads(
            encrypt_backup(b"payload", "a sufficiently long backup passphrase"),
        )
        envelope["kdf"]["n"] = 1 << 20
        with self.assertRaisesRegex(MemoryBackupError, "unsupported_memory_backup_kdf"):
            decrypt_backup(
                json.dumps(envelope).encode(),
                "a sufficiently long backup passphrase",
            )

    def test_rejects_weak_or_invalid_passwords(self):
        for password in ("short", b"short", "", None):
            with self.subTest(password=password):
                with self.assertRaisesRegex(MemoryBackupError, "invalid_backup_password"):
                    encrypt_backup(b"payload", password)

    def test_rejects_empty_and_oversized_payloads(self):
        with self.assertRaisesRegex(MemoryBackupError, "invalid_backup_payload_size"):
            encrypt_backup(b"", "a sufficiently long backup passphrase")
        with self.assertRaisesRegex(MemoryBackupError, "invalid_backup_payload_size"):
            encrypt_backup(
                b"x" * (8 * 1024 * 1024 + 1),
                "a sufficiently long backup passphrase",
            )


if __name__ == "__main__":
    unittest.main()
