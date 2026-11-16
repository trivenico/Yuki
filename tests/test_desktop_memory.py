import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ui.desktop.memory import create_desktop_memory


class FakeBackend:
    priority = 1


class FakeKeyring:
    def __init__(self):
        self.values = {}

    def get_keyring(self):
        return FakeBackend()

    def get_password(self, service, account):
        return self.values.get((service, account))

    def set_password(self, service, account, value):
        self.values[(service, account)] = value


@unittest.skipUnless(importlib.util.find_spec("cryptography"), "cryptography optional extra is not installed")
class DesktopMemoryTests(unittest.TestCase):
    def test_desktop_memory_is_encrypted_and_uses_private_profile_path(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "profile"
            keyring = FakeKeyring()
            with patch("ui.desktop.memory.desktop_data_root", return_value=root):
                memory = create_desktop_memory(keyring)
                memory.enable()
                memory.remember("Use local models by default", category="preference")
                memory.disable()
                memory.enable_context()
                entries = memory.context_entries(query="local models")
            self.assertEqual(len(entries), 1)
            encrypted_file = root / "memory.enc.json"
            envelope = json.loads(encrypted_file.read_text(encoding="utf-8"))
            self.assertEqual(envelope["version"], 1)
            self.assertNotIn("Use local models by default", encrypted_file.read_text(encoding="utf-8"))
            self.assertTrue(keyring.values)
            self.assertNotEqual(str(encrypted_file), str(Path.cwd() / "yuki_memory.json"))
            if os.name == "posix":
                self.assertEqual(encrypted_file.stat().st_mode & 0o777, 0o600)
                self.assertEqual(root.stat().st_mode & 0o777, 0o700)

    def test_desktop_memory_requires_a_keyring_module(self):
        with self.assertRaises(TypeError):
            create_desktop_memory(object())


if __name__ == "__main__":
    unittest.main()
