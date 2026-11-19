import tempfile
import unittest
from pathlib import Path

from automation.workflow_approval import WorkflowApprovalService
from automation.workflow_store import WorkflowRunStore
from core.auth import (AuthenticationError, BuiltInAuthService, observation_review_authorizer, workflow_principal_resolver)


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
        self.assertIn("observation:review:read", admin.permissions)
        self.assertIn("observation:review:decide", admin.permissions)
        self.assertIn("observation:review:delete", admin.permissions)
        operator = self.auth.create_account("ops.user", "Another secure password 4!")
        with self.auth._connect() as db:
            db.execute("UPDATE auth_users SET role='operator' WHERE user_id=?", (operator.user_id,))
        operator = next(user for user in self.auth.list_users() if user.user_id == operator.user_id)
        self.assertIn("observation:review:read", operator.permissions)
        self.assertIn("observation:review:decide", operator.permissions)
        self.assertNotIn("observation:review:delete", operator.permissions)
        with self.assertRaises(PermissionError):
            self.auth.bootstrap_admin("other.admin", "Correct horse battery staple 9!")
        user = self.auth.create_account("normal.user", "Another secure password 4!")
        self.assertEqual(user.role, "user")
        self.assertNotIn("workflow:approve", user.permissions)
        self.assertNotIn("observation:review:read", user.permissions)
        self.assertNotIn("observation:review:decide", user.permissions)
        self.assertNotIn("observation:review:delete", user.permissions)

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

    def test_invalid_credentials_are_generic_and_lock_account(self):
        self.auth.create_account("alice.user", "A sufficiently long password 3!")
        for _ in range(3):
            with self.assertRaises(AuthenticationError):
                self.auth.authenticate("alice.user", "wrong password")
        with self.assertRaises(AuthenticationError):
            self.auth.authenticate("alice.user", "A sufficiently long password 3!")

    def test_disabled_account_sessions_are_rejected(self):
        user = self.auth.create_account("alice.user", "A sufficiently long password 3!")
        session = self.auth.authenticate("alice.user", "A sufficiently long password 3!")
        self.assertTrue(self.auth.disable_user(user.user_id))
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

    def test_observation_review_authorization_is_bound_to_live_session_and_actor(self):
        admin = self.auth.bootstrap_admin("root.user", "Correct horse battery staple 9!")
        session = self.auth.authenticate("root.user", "Correct horse battery staple 9!")
        token = [session.token]
        authorize = observation_review_authorizer(self.auth, lambda: token[0])
        self.assertTrue(authorize(admin.user_id, "observation:review:read"))
        self.assertTrue(authorize(admin.user_id, "observation:review:decide"))
        self.assertTrue(authorize(admin.user_id, "observation:review:delete"))
        self.assertFalse(authorize("forged-actor", "observation:review:decide"))
        self.assertFalse(authorize(admin.user_id, "workflow:approve"))
        self.auth.revoke_session(session.token)
        self.assertFalse(authorize(admin.user_id, "observation:review:read"))

    def test_knowledge_promotion_permission_is_admin_only_and_session_bound(self):
        admin = self.auth.bootstrap_admin("root.user", "Correct horse battery staple 9!")
        admin_session = self.auth.authenticate("root.user", "Correct horse battery staple 9!")
        admin_authorize = observation_review_authorizer(self.auth, lambda: admin_session.token)
        self.assertTrue(admin_authorize(admin.user_id, "memory:knowledge:promote"))

        operator = self.auth.create_account("ops.user", "Another secure password 4!")
        with self.auth._connect() as db:
            db.execute("UPDATE auth_users SET role='operator' WHERE user_id=?", (operator.user_id,))
        operator = next(user for user in self.auth.list_users() if user.user_id == operator.user_id)
        operator_session = self.auth.authenticate("ops.user", "Another secure password 4!")
        operator_authorize = observation_review_authorizer(self.auth, lambda: operator_session.token)
        self.assertFalse(operator_authorize(operator.user_id, "memory:knowledge:promote"))
        self.assertFalse(admin_authorize("forged-actor", "memory:knowledge:promote"))
        self.auth.revoke_session(admin_session.token)
        self.assertFalse(admin_authorize(admin.user_id, "memory:knowledge:promote"))

    def test_observation_review_roles_are_least_privilege(self):
        self.auth.bootstrap_admin("root.user", "Correct horse battery staple 9!")
        operator = self.auth.create_account("ops.user", "Another secure password 4!")
        with self.auth._connect() as db:
            db.execute("UPDATE auth_users SET role='operator' WHERE user_id=?", (operator.user_id,))
        operator = next(user for user in self.auth.list_users() if user.user_id == operator.user_id)
        self.assertIn("observation:review:read", operator.permissions)
        self.assertIn("observation:review:decide", operator.permissions)
        self.assertNotIn("observation:review:delete", operator.permissions)
        regular = self.auth.create_account("normal.user", "Another secure password 5!")
        self.assertNotIn("observation:review:read", regular.permissions)


if __name__ == "__main__":
    unittest.main()
