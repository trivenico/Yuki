import tempfile
import unittest
from pathlib import Path

from core.profile_identity import (
    ProfileIdentityError,
    bind_profile_to_current_os_user,
)


class ProfileOwnerBindingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_first_os_account_claims_profile_and_same_owner_can_reopen(self):
        bind_profile_to_current_os_user(self.root, principal="test-os-account:owner")
        bind_profile_to_current_os_user(self.root, principal="test-os-account:owner")
        self.assertTrue((self.root / "profile_owner.json").is_file())
        raw = (self.root / "profile_owner.json").read_text(encoding="utf-8")
        self.assertNotIn("test-os-account:owner", raw)

    def test_different_os_account_is_denied_without_overwriting_binding(self):
        bind_profile_to_current_os_user(self.root, principal="test-os-account:owner")
        before = (self.root / "profile_owner.json").read_bytes()
        with self.assertRaisesRegex(ProfileIdentityError, "different OS account"):
            bind_profile_to_current_os_user(self.root, principal="test-os-account:intruder")
        self.assertEqual((self.root / "profile_owner.json").read_bytes(), before)

    def test_invalid_profile_root_is_rejected(self):
        missing = self.root / "missing"
        with self.assertRaises(ProfileIdentityError):
            bind_profile_to_current_os_user(missing, principal="test-os-account:owner")


if __name__ == "__main__":
    unittest.main()
