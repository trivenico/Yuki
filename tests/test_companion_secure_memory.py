import base64
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from companion.secure_memory import EncryptedMemoryStore


class FakeKeyring:
    def __init__(self):
        self.values = {}
        self.backend = MagicMock(priority=1)

    def get_keyring(self):
        return self.backend

    def get_password(self, service, account):
        return self.values.get((service, account))

    def set_password(self, service, account, value):
        self.values[(service, account)] = value


class EncryptedMemoryTests(unittest.TestCase):
    def setUp(self):
        try:
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        except ImportError:
            self.skipTest("optional cryptography dependency not installed")
        self.aesgcm = AESGCM
        self.keyring = FakeKeyring()
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "memory.enc.json"
        self.store = EncryptedMemoryStore(self.path, keyring_module=self.keyring, aesgcm_class=AESGCM)

    def tearDown(self):
        self.temp.cleanup()

    def test_round_trip_is_encrypted_and_key_is_outside_file(self):
        saved = self.store.add("private memory")
        raw = self.path.read_text(encoding="utf-8")
        self.assertNotIn("private memory", raw)
        self.assertNotIn(base64.b64decode(self.keyring.get_password("yuki-companion-memory", "default")).hex(), raw)
        reopened = EncryptedMemoryStore(self.path, keyring_module=self.keyring, aesgcm_class=self.aesgcm)
        self.assertEqual(reopened.list()[0]["id"], saved["id"])
        self.assertEqual(reopened.list()[0]["text"], "private memory")

    def test_tampering_fails_closed_and_preserves_ciphertext(self):
        self.store.add("do not lose me")
        envelope = json.loads(self.path.read_text(encoding="utf-8"))
        envelope["ciphertext"] = base64.b64encode(b"x" * 64).decode("ascii")
        self.path.write_text(json.dumps(envelope), encoding="utf-8")
        tampered = self.path.read_text(encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "authentication failed"):
            self.store.list()
        self.assertEqual(self.path.read_text(encoding="utf-8"), tampered)

    def test_existing_ciphertext_without_key_does_not_create_replacement_key(self):
        self.store.add("preserve encrypted history")
        original = self.path.read_bytes()
        self.keyring.values.clear()
        with self.assertRaisesRegex(RuntimeError, "Could not safely access"):
            EncryptedMemoryStore(
                self.path, keyring_module=self.keyring, aesgcm_class=self.aesgcm,
            )
        self.assertEqual(self.keyring.values, {})
        self.assertEqual(self.path.read_bytes(), original)

    def test_profile_scoped_keys_are_isolated(self):
        with tempfile.TemporaryDirectory() as directory:
            first_path = Path(directory) / "one.enc.json"
            second_path = Path(directory) / "two.enc.json"
            first = EncryptedMemoryStore(
                first_path, keyring_module=self.keyring, aesgcm_class=self.aesgcm,
                keyring_account="profile:one",
            )
            second = EncryptedMemoryStore(
                second_path, keyring_module=self.keyring, aesgcm_class=self.aesgcm,
                keyring_account="profile:two",
            )
            first.add("first profile secret")
            second.add("second profile secret")
            first_key = self.keyring.get_password("yuki-companion-memory", "profile:one")
            second_key = self.keyring.get_password("yuki-companion-memory", "profile:two")
            self.assertNotEqual(first_key, second_key)
            self.assertEqual(
                EncryptedMemoryStore(
                    first_path, keyring_module=self.keyring, aesgcm_class=self.aesgcm,
                    keyring_account="profile:one",
                ).list()[0]["text"],
                "first profile secret",
            )
            with self.assertRaisesRegex(RuntimeError, "authentication failed"):
                EncryptedMemoryStore(
                    first_path, keyring_module=self.keyring, aesgcm_class=self.aesgcm,
                    keyring_account="profile:two",
                ).list()

    def test_existing_legacy_key_is_migrated_to_profile_account_without_changing_ciphertext(self):
        legacy = EncryptedMemoryStore(
            self.path, keyring_module=self.keyring, aesgcm_class=self.aesgcm,
        )
        legacy.add("preserve old memory")
        original = self.path.read_bytes()
        legacy_key = self.keyring.get_password("yuki-companion-memory", "default")
        migrated = EncryptedMemoryStore(
            self.path, keyring_module=self.keyring, aesgcm_class=self.aesgcm,
            keyring_account="profile:existing",
        )
        self.assertEqual(
            self.keyring.get_password("yuki-companion-memory", "profile:existing"),
            legacy_key,
        )
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(migrated.list()[0]["text"], "preserve old memory")

    def test_existing_memory_without_legacy_or_profile_key_still_fails_closed(self):
        self.path.write_text('{"version":1,"nonce":"AAAAAAAAAAAAAAAA","ciphertext":"AAAAAAAAAAAAAAAAAAAAAA=="}')
        # setUp creates the default account for the fixture initial empty store.
        # Clear it to model a genuinely missing legacy and profile key.
        self.keyring.values.clear()
        with self.assertRaisesRegex(RuntimeError, "Could not safely access"):
            EncryptedMemoryStore(
                self.path, keyring_module=self.keyring, aesgcm_class=self.aesgcm,
                keyring_account="profile:missing",
            )
        self.assertNotIn(("yuki-companion-memory", "profile:missing"), self.keyring.values)

    def test_invalid_keyring_backend_fails_closed(self):
        keyring = FakeKeyring()
        keyring.backend.priority = 0
        with self.assertRaisesRegex(RuntimeError, "secure operating-system keyring"):
            EncryptedMemoryStore(self.path, keyring_module=keyring, aesgcm_class=self.aesgcm)


if __name__ == "__main__":
    unittest.main()
