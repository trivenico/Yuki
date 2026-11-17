import json
import os
import tempfile
import unittest
from pathlib import Path

from core.profile_identity import (
    ProfileIdentityError,
    load_or_create_profile_id,
    profile_memory_keyring_account,
)


class ProfileIdentityTests(unittest.TestCase):
    def test_identity_is_stable_and_private(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = load_or_create_profile_id(root)
            second = load_or_create_profile_id(root)
            self.assertEqual(first, second)
            path = root / "profile_identity.json"
            self.assertEqual(json.loads(path.read_text())["profile_id"], first)
            if os.name == "posix":
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_separate_profiles_receive_distinct_identities(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = load_or_create_profile_id(root / "one")
            second = load_or_create_profile_id(root / "two")
            self.assertNotEqual(first, second)
            self.assertNotEqual(
                profile_memory_keyring_account(first),
                profile_memory_keyring_account(second),
            )

    def test_invalid_existing_identity_is_preserved_and_not_replaced(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "profile_identity.json"
            original = b'{"schema_version":1,"profile_id":"not-a-uuid"}'
            path.write_bytes(original)
            with self.assertRaises(ProfileIdentityError):
                load_or_create_profile_id(root)
            self.assertEqual(path.read_bytes(), original)

    def test_symlink_identity_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "target.json"
            target.write_text('{"schema_version":1,"profile_id":"00000000-0000-4000-8000-000000000000"}')
            link = root / "profile_identity.json"
            try:
                link.symlink_to(target)
            except OSError:
                self.skipTest("symlinks unavailable")
            with self.assertRaises(ProfileIdentityError):
                load_or_create_profile_id(root)


if __name__ == "__main__":
    unittest.main()
