import tempfile
import unittest
from pathlib import Path

from core.auth import AuthenticationError, BuiltInAuthService
from ui.desktop.auth import DesktopSessionManager
from ui.desktop.bootstrap import AuthenticatedDesktopIdentity, DesktopStartupError


class FakeKeyring:
    def __init__(self):
        self.values = {}

    def set_password(self, service, account, value):
        self.values[(service, account)] = value

    def get_password(self, service, account):
        return self.values.get((service, account))

    def delete_password(self, service, account):
        del self.values[(service, account)]


class FailingKeyring(FakeKeyring):
    def set_password(self, service, account, value):
        raise RuntimeError("keyring unavailable")


class DesktopSessionManagerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.auth = BuiltInAuthService(
            str(Path(self.tmp.name) / "auth.sqlite3"),
            password_iterations=100_000,
        )
        self.user = self.auth.bootstrap_admin(
            "desktop.admin", "A strong desktop password 4!"
        )
        self.keyring = FakeKeyring()
        self.manager = DesktopSessionManager(self.auth, self.keyring)

    def test_login_stores_only_opaque_token_and_resolves_identity(self):
        identity = self.manager.login("desktop.admin", "A strong desktop password 4!")
        self.assertIsInstance(identity, AuthenticatedDesktopIdentity)
        self.assertEqual(identity.actor_id, self.user.user_id)
        self.assertEqual(self.manager.current_identity(), identity)
        self.assertNotIn("A strong desktop password 4!", repr(self.keyring.values))

    def test_invalid_password_does_not_create_keyring_entry(self):
        with self.assertRaises(AuthenticationError):
            self.manager.login("desktop.admin", "incorrect password")
        self.assertEqual(self.keyring.values, {})

    def test_logout_revokes_session_and_deletes_keyring_entry(self):
        self.manager.login("desktop.admin", "A strong desktop password 4!")
        identity = self.manager.current_identity()
        self.assertTrue(self.manager.logout())
        self.assertIsNone(self.manager.current_identity())
        self.assertIsNone(self.auth.resolve_session(identity.session_id))

    def test_keyring_failure_revokes_new_session(self):
        manager = DesktopSessionManager(self.auth, FailingKeyring())
        with self.assertRaises(DesktopStartupError):
            manager.login("desktop.admin", "A strong desktop password 4!")
        with self.auth._connect() as db:
            active = db.execute(
                "SELECT COUNT(*) AS count FROM auth_sessions WHERE revoked_at IS NULL"
            ).fetchone()["count"]
        self.assertEqual(active, 0)

    def test_revoked_or_expired_keyring_token_is_not_authenticated(self):
        identity = self.manager.login("desktop.admin", "A strong desktop password 4!")
        self.auth.revoke_session(identity.session_id)
        self.assertIsNone(self.manager.current_identity())


if __name__ == "__main__":
    unittest.main()
