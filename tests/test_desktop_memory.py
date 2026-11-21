import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ui.desktop.memory import create_desktop_memory
from core.profile_identity import load_or_create_profile_id, account_memory_keyring_account, account_memory_filename


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
                memory = create_desktop_memory(keyring, actor_id="alice")
                memory.enable()
                memory.remember("Use local models by default", category="preference")
                memory.disable()
                memory.enable_context()
                entries = memory.context_entries(query="local models")
            self.assertEqual(len(entries), 1)
            profile_id = load_or_create_profile_id(root)
            encrypted_file = root / account_memory_filename(profile_id, "alice")
            envelope = json.loads(encrypted_file.read_text(encoding="utf-8"))
            self.assertEqual(envelope["version"], 1)
            self.assertNotIn("Use local models by default", encrypted_file.read_text(encoding="utf-8"))
            self.assertTrue(keyring.values)
            self.assertNotEqual(str(encrypted_file), str(Path.cwd() / "yuki_memory.json"))
            if os.name == "posix":
                self.assertEqual(encrypted_file.stat().st_mode & 0o777, 0o600)
                self.assertEqual(root.stat().st_mode & 0o777, 0o700)

    def test_each_desktop_profile_uses_its_own_keyring_account(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            keyring = FakeKeyring()
            with patch("ui.desktop.memory.desktop_data_root", return_value=root / "one"):
                create_desktop_memory(keyring, actor_id="alice")
            with patch("ui.desktop.memory.desktop_data_root", return_value=root / "two"):
                create_desktop_memory(keyring, actor_id="alice")
            first_id = load_or_create_profile_id(root / "one")
            second_id = load_or_create_profile_id(root / "two")
            first_account = account_memory_keyring_account(first_id, "alice")
            second_account = account_memory_keyring_account(second_id, "alice")
            self.assertNotEqual(first_account, second_account)
            self.assertIn(("yuki-companion-memory", first_account), keyring.values)
            self.assertIn(("yuki-companion-memory", second_account), keyring.values)
            self.assertNotEqual(
                keyring.values[("yuki-companion-memory", first_account)],
                keyring.values[("yuki-companion-memory", second_account)],
            )


    def test_two_accounts_in_one_profile_get_distinct_files_and_keys(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "profile"
            keyring = FakeKeyring()
            with patch("ui.desktop.memory.desktop_data_root", return_value=root):
                alice = create_desktop_memory(keyring, actor_id="alice")
                bob = create_desktop_memory(keyring, actor_id="bob")
            profile_id = load_or_create_profile_id(root)
            alice_path = root / account_memory_filename(profile_id, "alice")
            bob_path = root / account_memory_filename(profile_id, "bob")
            self.assertNotEqual(alice_path, bob_path)
            alice.enable()
            alice.remember("alice private memory")
            bob.enable()
            bob.remember("bob private memory")
            self.assertTrue(alice_path.exists())
            self.assertTrue(bob_path.exists())
            self.assertNotEqual(
                keyring.values[("yuki-companion-memory", account_memory_keyring_account(profile_id, "alice"))],
                keyring.values[("yuki-companion-memory", account_memory_keyring_account(profile_id, "bob"))],
            )

    def test_desktop_memory_requires_authenticated_actor(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "profile"
            with patch("ui.desktop.memory.desktop_data_root", return_value=root):
                with self.assertRaises(ValueError):
                    create_desktop_memory(FakeKeyring(), actor_id="")

    def test_account_memory_keyring_label_does_not_expose_actor_id(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "profile"
            root.mkdir()
            profile_id = load_or_create_profile_id(root)
            account = account_memory_keyring_account(profile_id, "alice@example.invalid")
            filename = account_memory_filename(profile_id, "alice@example.invalid")
            self.assertNotIn("alice", account)
            self.assertNotIn("example", filename)

    def test_desktop_memory_requires_a_keyring_module(self):
        with self.assertRaises(TypeError):
            create_desktop_memory(object())


if __name__ == "__main__":
    unittest.main()
