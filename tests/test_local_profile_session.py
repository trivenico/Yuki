import tempfile
import unittest
from pathlib import Path

from core.auth import BuiltInAuthService
from ui.desktop.auth import DesktopSessionManager


class MemoryKeyring:
    def __init__(self):
        self.values = {}

    def get_password(self, service, account):
        return self.values.get((service, account))

    def set_password(self, service, account, value):
        self.values[(service, account)] = value

    def delete_password(self, service, account):
        self.values.pop((service, account), None)


class LocalProfileSessionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.auth = BuiltInAuthService(
            str(Path(self.temp.name) / "auth.sqlite3"),
            password_iterations=100_000,
            session_ttl_seconds=3600,
        )
        self.keyring = MemoryKeyring()
        self.sessions = DesktopSessionManager(self.auth, self.keyring)

    def test_first_launch_creates_one_local_owner_and_opens_session(self):
        identity = self.sessions.open_local_profile_session()
        self.assertTrue(identity.actor_id)
        self.assertTrue(identity.session_id)
        self.assertEqual(len(self.auth.list_users()), 1)
        self.assertEqual(self.auth.list_users()[0].username, "yuki-local")
        self.assertEqual(self.auth.resolve_session(identity.session_id).user_id, identity.actor_id)
        self.assertEqual(
            self.keyring.get_password("yuki.desktop", "active-session"),
            identity.session_id,
        )

    def test_each_launch_rotates_the_previous_keyring_session(self):
        first = self.sessions.open_local_profile_session()
        second = self.sessions.open_local_profile_session()
        self.assertNotEqual(first.session_id, second.session_id)
        self.assertIsNone(self.auth.resolve_session(first.session_id))
        self.assertEqual(self.auth.resolve_session(second.session_id).user_id, second.actor_id)

    def test_existing_single_account_is_reused_without_a_sign_in_form(self):
        user = self.auth.bootstrap_admin("owner.account", "A strong legacy password 7!")
        identity = self.sessions.open_local_profile_session()
        self.assertEqual(identity.actor_id, user.user_id)
        self.assertEqual(len(self.auth.list_users()), 1)

    def test_multiple_accounts_fail_closed(self):
        self.auth.bootstrap_admin("owner.account", "A strong legacy password 7!")
        self.auth.create_account("second.account", "Another strong password 8!")
        with self.assertRaisesRegex(RuntimeError, "exactly one enabled local account"):
            self.sessions.open_local_profile_session()
        self.assertIsNone(self.keyring.get_password("yuki.desktop", "active-session"))


if __name__ == "__main__":
    unittest.main()
