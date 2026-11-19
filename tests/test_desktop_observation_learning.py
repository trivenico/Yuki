import tempfile
import unittest
from pathlib import Path

from core.auth import BuiltInAuthService
from ui.desktop.auth import DesktopSessionManager
from ui.desktop.bootstrap import AuthenticatedDesktopIdentity
from ui.desktop.observation_learning import build_desktop_observation_review_service


class _Keyring:
    def __init__(self):
        self.values = {}

    def get_password(self, service, account):
        return self.values.get((service, account))

    def set_password(self, service, account, value):
        self.values[(service, account)] = value

    def delete_password(self, service, account):
        self.values.pop((service, account), None)


class DesktopObservationReviewCompositionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.auth = BuiltInAuthService(
            str(Path(self.tmp.name) / "auth.sqlite3"),
            password_iterations=100_000,
        )
        self.user = self.auth.bootstrap_admin("root.user", "Correct horse battery staple 9!")
        self.keyring = _Keyring()
        self.sessions = DesktopSessionManager(self.auth, self.keyring)
        self.identity = self.sessions.login("root.user", "Correct horse battery staple 9!")
        self.database = Path(self.tmp.name) / "observation-review.sqlite3"

    def build(self, identity=None):
        return build_desktop_observation_review_service(
            self.auth, self.sessions, identity or self.identity, self.database,
        )

    def test_composes_owner_scoped_service_from_live_identity(self):
        service = self.build()
        proposal = service._store.propose("application", "com.example.editor")
        self.assertEqual(
            service.list_pending(actor_id=self.user.user_id)[0].proposal_id,
            proposal.proposal_id,
        )

    def test_rejects_identity_from_another_or_stale_session(self):
        other = AuthenticatedDesktopIdentity("other-user", self.identity.session_id)
        with self.assertRaises(PermissionError):
            self.build(other)
        self.sessions.logout()
        with self.assertRaises(PermissionError):
            self.build()

    def test_existing_service_fails_closed_after_logout(self):
        service = self.build()
        self.sessions.logout()
        with self.assertRaises(PermissionError):
            service.list_pending(actor_id=self.user.user_id)


if __name__ == "__main__":
    unittest.main()
