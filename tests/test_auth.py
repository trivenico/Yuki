import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from pathlib import Path

from automation.workflow_approval import WorkflowApprovalService
from automation.workflow_store import WorkflowRunStore
from core.auth import (
    AuthenticationError,
    BuiltInAuthService,
    execution_tool_principal_resolver,
    workflow_principal_resolver,
)


class BuiltInAuthServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.auth = BuiltInAuthService(
            str(Path(self.tmp.name) / "auth.sqlite3"),
            password_iterations=100_000,
            session_ttl_seconds=3600,
            max_failed_logins=3,
            lockout_seconds=30,
        )

    def test_first_admin_bootstrap_is_one_time_and_signup_is_least_privilege(self):
        admin = self.auth.bootstrap_admin("root.user", "Correct horse battery staple 9!")
        self.assertIn("users:manage", admin.permissions)
        with self.assertRaises(PermissionError):
            self.auth.bootstrap_admin("other.admin", "Correct horse battery staple 9!")
        user = self.auth.create_account("normal.user", "Another secure password 4!")
        self.assertEqual(user.role, "user")
        self.assertNotIn("workflow:approve", user.permissions)

    def test_login_stores_only_session_hash_and_logout_revokes(self):
        self.auth.create_account("alice.user", "A sufficiently long password 3!")
        session = self.auth.authenticate("ALICE.USER", "A sufficiently long password 3!")
        self.assertTrue(session.token)
        self.assertEqual(self.auth.resolve_session(session.token).username, "alice.user")
        with self.auth._connect() as db:
            row = db.execute("SELECT token_hash FROM auth_sessions").fetchone()
            self.assertNotEqual(row["token_hash"], session.token)
        self.assertTrue(self.auth.revoke_session(session.token))
        self.assertIsNone(self.auth.resolve_session(session.token))
        self.assertFalse(self.auth.revoke_session(session.token))


    def test_execution_session_fingerprint_resolves_without_bearer_token(self):
        from core.auth import execution_authenticated_context_resolver
        user = self.auth.create_account("fingerprint.user", "A sufficiently long password 7!")
        session = self.auth.authenticate("fingerprint.user", "A sufficiently long password 7!")
        fingerprint = __import__("hashlib").sha256(session.token.encode("utf-8")).hexdigest()
        verify = execution_authenticated_context_resolver(self.auth)
        request = type("Request", (), {"actor_id": user.user_id, "session_id": fingerprint})()
        self.assertTrue(verify(request))
        request.actor_id = "another-account"
        self.assertFalse(verify(request))
        request.actor_id = user.user_id
        request.session_id = session.token
        self.assertFalse(verify(request))
        self.assertIsNone(self.auth.resolve_session_fingerprint(session.token))


    def test_execution_tool_principal_uses_live_auth_and_explicit_permissions(self):
        user = self.auth.create_account("tool.reader", "A sufficiently long password 9!")
        session = self.auth.authenticate("tool.reader", "A sufficiently long password 9!")
        fingerprint = __import__("hashlib").sha256(session.token.encode("utf-8")).hexdigest()
        policy_state = {"allow_read": True}
        resolver = execution_tool_principal_resolver(
            self.auth,
            permissions_for_user=lambda authenticated: (
                frozenset({"workspace:read"})
                if authenticated.user_id == user.user_id and policy_state["allow_read"]
                else frozenset()
            ),
        )
        request = type("Request", (), {
            "actor_id": user.user_id,
            "session_id": fingerprint,
        })()
        principal = resolver(request)
        self.assertEqual(principal.actor_id, user.user_id)
        self.assertEqual(principal.permissions, frozenset({"workspace:read"}))

        policy_state["allow_read"] = False
        self.assertEqual(resolver(request).permissions, frozenset())

        policy_state["allow_read"] = True
        self.auth.revoke_session(session.token)
        with self.assertRaisesRegex(PermissionError, "no longer active"):
            resolver(request)

    def test_execution_tool_principal_rejects_cross_account_and_raw_token(self):
        user = self.auth.create_account("tool.owner", "A sufficiently long password 6!")
        session = self.auth.authenticate("tool.owner", "A sufficiently long password 6!")
        resolver = execution_tool_principal_resolver(
            self.auth,
            permissions_for_user=lambda _user: frozenset({"workspace:read"}),
        )
        request = type("Request", (), {
            "actor_id": "different-account",
            "session_id": __import__("hashlib").sha256(session.token.encode("utf-8")).hexdigest(),
        })()
        with self.assertRaisesRegex(PermissionError, "no longer active"):
            resolver(request)
        request.actor_id = user.user_id
        request.session_id = session.token
        with self.assertRaisesRegex(PermissionError, "no longer active"):
            resolver(request)

    def test_execution_session_fingerprint_fails_after_logout(self):
        from core.auth import execution_authenticated_context_resolver
        user = self.auth.create_account("logout.user", "A sufficiently long password 8!")
        session = self.auth.authenticate("logout.user", "A sufficiently long password 8!")
        fingerprint = __import__("hashlib").sha256(session.token.encode("utf-8")).hexdigest()
        verify = execution_authenticated_context_resolver(self.auth)
        request = type("Request", (), {"actor_id": user.user_id, "session_id": fingerprint})()
        self.assertTrue(verify(request))
        self.auth.revoke_session(session.token)
        self.assertFalse(verify(request))

    def test_invalid_credentials_are_generic_and_lock_account(self):
        self.auth.create_account("alice.user", "A sufficiently long password 3!")
        for _ in range(3):
            with self.assertRaises(AuthenticationError):
                self.auth.authenticate("alice.user", "wrong password")
        with self.assertRaises(AuthenticationError):
            self.auth.authenticate("alice.user", "A sufficiently long password 3!")

    def test_lockout_expiry_starts_a_fresh_failed_login_window(self):
        self.auth.create_account("alice.user", "A sufficiently long password 3!")
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        with patch(
            "core.auth._utcnow",
            side_effect=[
                start,
                start,
                start,
                start + timedelta(seconds=self.auth.lockout_seconds + 1),
            ],
        ):
            for _ in range(self.auth.max_failed_logins):
                with self.assertRaises(AuthenticationError):
                    self.auth.authenticate("alice.user", "wrong password")
            with self.assertRaises(AuthenticationError):
                self.auth.authenticate("alice.user", "wrong password")

        with self.auth._connect() as db:
            row = db.execute(
                "SELECT failed_logins, locked_until FROM auth_users WHERE username=?",
                ("alice.user",),
            ).fetchone()
        self.assertEqual(row["failed_logins"], 1)
        self.assertIsNone(row["locked_until"])

    def test_disabled_account_sessions_are_rejected(self):
        user = self.auth.create_account("alice.user", "A sufficiently long password 3!")
        session = self.auth.authenticate("alice.user", "A sufficiently long password 3!")
        self.assertTrue(self.auth.disable_user(user.user_id))
        self.assertIsNone(self.auth.get_user(user.user_id))
        self.assertIsNone(self.auth.resolve_session(session.token))

    def test_password_and_configuration_validation(self):
        with self.assertRaises(ValueError):
            self.auth.create_account("ab", "too short")
        with self.assertRaises(ValueError):
            BuiltInAuthService(str(Path(self.tmp.name) / "bad.sqlite3"), password_iterations=1)

    def test_workflow_approval_uses_authenticated_identity_not_request_claims(self):
        admin = self.auth.bootstrap_admin("root.user", "Correct horse battery staple 9!")
        session = self.auth.authenticate("root.user", "Correct horse battery staple 9!")
        store = WorkflowRunStore(str(Path(self.tmp.name) / "workflow.sqlite3"))
        run = store.create(
            idempotency_key="auth-bound-approval",
            workflow_name="release",
            definition=[{"name": "publish", "action": "emit", "argument": "release", "requires_approval": True}],
        )
        service = WorkflowApprovalService(store, principal_resolver=workflow_principal_resolver(self.auth))
        approved = service.approve(run.run_id, request_context=session.token, expected_version=run.version)
        self.assertEqual(approved.approved_by, admin.user_id)
        with self.assertRaises(AuthenticationError):
            service.approve(run.run_id, request_context="forged-user-token", expected_version=approved.version)


if __name__ == "__main__":
    unittest.main()
