import tempfile
import unittest
from pathlib import Path

from core.auth import BuiltInAuthService
from core.identity import AuthenticatedIdentity
from core.permissions import PermissionGate


class AuthenticatedIdentityTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.auth = BuiltInAuthService(
            str(Path(self.tempdir.name) / "auth.sqlite3"),
            password_iterations=100_000,
        )

    def tearDown(self):
        self.tempdir.cleanup()

    def test_identity_is_derived_from_resolved_session_and_never_contains_bearer(self):
        self.auth.bootstrap_admin("identity-admin", "a-long-test-password-123")
        issued = self.auth.authenticate("identity-admin", "a-long-test-password-123")
        resolved = self.auth.resolve_session(issued.token)
        identity = AuthenticatedIdentity.from_session(self.auth, issued.token)

        self.assertEqual(identity.actor_id, resolved.user_id)
        self.assertEqual(identity.role, "admin")
        self.assertTrue(identity.is_admin)
        self.assertNotEqual(identity.session_id, issued.token)
        self.assertEqual(len(identity.session_id), 64)
        self.assertNotIn(issued.token, repr(identity))

    def test_permission_gate_uses_verified_identity_and_role(self):
        self.auth.bootstrap_admin("identity-admin", "a-long-test-password-123")
        issued = self.auth.authenticate("identity-admin", "a-long-test-password-123")
        identity = AuthenticatedIdentity.from_session(self.auth, issued.token)
        gate = PermissionGate.from_authenticated_identity(identity, lambda request: True)
        self.assertEqual(gate.actor_id, identity.actor_id)
        self.assertEqual(gate.session_id, identity.session_id)
        self.assertTrue(gate.is_admin)

    def test_end_user_cannot_construct_privileged_permission_gate(self):
        self.auth.create_account("ordinary-user", "a-long-test-password-123")
        issued = self.auth.authenticate("ordinary-user", "a-long-test-password-123")
        identity = AuthenticatedIdentity.from_session(self.auth, issued.token)
        with self.assertRaises(PermissionError):
            PermissionGate.from_authenticated_identity(identity, lambda request: True)

    def test_unverified_identity_object_is_rejected(self):
        class ForgedIdentity:
            actor_id = "attacker"
            session_id = "forged-session"
            role = "admin"

        with self.assertRaises(TypeError):
            PermissionGate.from_authenticated_identity(ForgedIdentity(), lambda request: True)

    def test_revoked_session_cannot_produce_identity(self):
        self.auth.bootstrap_admin("identity-admin", "a-long-test-password-123")
        issued = self.auth.authenticate("identity-admin", "a-long-test-password-123")
        self.auth.revoke_session(issued.token)
        with self.assertRaises(ValueError):
            AuthenticatedIdentity.from_session(self.auth, issued.token)

    def test_invalid_session_token_is_rejected(self):
        self.auth.bootstrap_admin("identity-admin", "a-long-test-password-123")
        issued = self.auth.authenticate("identity-admin", "a-long-test-password-123")
        resolved = self.auth.resolve_session(issued.token)
        with self.assertRaises(ValueError):
            AuthenticatedIdentity.from_session(self.auth, "short")


if __name__ == "__main__":
    unittest.main()
