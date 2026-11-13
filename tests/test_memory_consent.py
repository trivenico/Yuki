import tempfile
import unittest
from pathlib import Path

from core.memory_consent import MemoryConsentStore


class MemoryConsentStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "private" / "consent.sqlite3"
        self.store = MemoryConsentStore(self.path)

    def tearDown(self):
        self.temp.cleanup()

    def test_consent_is_disabled_by_default_and_scoped_per_user(self):
        self.assertFalse(self.store.enabled("owner-a"))
        self.store.set_enabled("owner-a", True)
        self.assertTrue(self.store.enabled("owner-a"))
        self.assertFalse(self.store.enabled("owner-b"))

    def test_consent_can_be_revoked_without_deleting_other_users_state(self):
        self.store.set_enabled("owner-a", True)
        self.store.set_enabled("owner-b", True)
        self.store.set_enabled("owner-a", False)
        self.assertFalse(self.store.enabled("owner-a"))
        self.assertTrue(self.store.enabled("owner-b"))

    def test_database_is_created_with_private_posix_permissions(self):
        self.assertTrue(self.path.is_file())
        if __import__("os").name == "posix":
            self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_rejects_invalid_identity_and_consent_values(self):
        for user_id in ("", "  ", "owner\nforged", "owner\x00id"):
            with self.subTest(user_id=repr(user_id)), self.assertRaises(ValueError):
                self.store.enabled(user_id)
        with self.assertRaises(ValueError):
            self.store.set_enabled("owner-a", 1)


if __name__ == "__main__":
    unittest.main()
