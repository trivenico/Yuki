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

    def test_invalid_keyring_backend_fails_closed(self):
        keyring = FakeKeyring()
        keyring.backend.priority = 0
        with self.assertRaisesRegex(RuntimeError, "secure operating-system keyring"):
            EncryptedMemoryStore(self.path, keyring_module=keyring, aesgcm_class=self.aesgcm)


if __name__ == "__main__":
    unittest.main()
