import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from pathlib import Path

from automation.workflow_approval import WorkflowApprovalService
from automation.workflow_store import WorkflowRunStore
from core.auth import AuthenticationError, BuiltInAuthService, workflow_principal_resolver


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

    def test_invalid_credentials_are_generic_and_lock_account(self):
        self.auth.create_account("alice.user", "A sufficiently long password 3!")
        for _ in range(3):
            with self.assertRaises(AuthenticationError):
                self.auth.authenticate("alice.user", "wrong password")
        with self.assertRaises(AuthenticationError):
            self.auth.authenticate("alice.user", "A sufficiently long password 3!")

    def test_concurrent_failed_logins_cannot_lose_lockout_increments(self):
        self.auth.create_account("alice.user", "A sufficiently long password 3!")

        def attempt(_):
            try:
                self.auth.authenticate("alice.user", "wrong password")
            except AuthenticationError:
                return False
            return True

        with ThreadPoolExecutor(max_workers=12) as pool:
            results = list(pool.map(attempt, range(12)))

        self.assertFalse(any(results))
        with self.assertRaises(AuthenticationError):
            self.auth.authenticate("alice.user", "A sufficiently long password 3!")
        with self.auth._connect() as db:
            row = db.execute(
                "SELECT failed_logins, locked_until FROM auth_users WHERE username=?",
                ("alice.user",),
            ).fetchone()
        self.assertGreaterEqual(row["failed_logins"], self.auth.max_failed_logins)
        self.assertIsNotNone(row["locked_until"])

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


    def test_task_permission_authorizer_is_bound_to_live_actor_and_role(self):
        from core.auth import task_permission_authorizer
        admin = self.auth.bootstrap_admin("root.user", "Correct horse battery staple 9!")
        session = self.auth.authenticate("root.user", "Correct horse battery staple 9!")
        current = [session.token]
        authorize = task_permission_authorizer(
            self.auth, session_token_provider=lambda: current[0],
        )
        self.assertTrue(authorize(admin.user_id, "task:list", ""))
        self.assertTrue(authorize(admin.user_id, "task:approve", "task-1"))
        self.assertFalse(authorize("forged-user", "task:list", ""))
        self.assertFalse(authorize(admin.user_id, "task:unknown", ""))
        current[0] = "invalid-token"
        self.assertFalse(authorize(admin.user_id, "task:list", ""))

    def test_task_permission_authorizer_rejects_reader_for_mutations(self):
        from core.auth import task_permission_authorizer
        user = self.auth.create_account("reader.user", "Another secure password 4!")
        session = self.auth.authenticate("reader.user", "Another secure password 4!")
        authorize = task_permission_authorizer(
            self.auth, session_token_provider=lambda: session.token,
        )
        self.assertTrue(authorize(user.user_id, "task:read", "task-1"))
        self.assertFalse(authorize(user.user_id, "task:approve", "task-1"))

    def test_agent_permission_authorizer_separates_workflow_and_registry_admin(self):
        from core.auth import agent_permission_authorizer
        admin = self.auth.bootstrap_admin("root.user", "Correct horse battery staple 9!")
        admin_session = self.auth.authenticate("root.user", "Correct horse battery staple 9!")
        current = [admin_session.token]
        authorize = agent_permission_authorizer(
            self.auth, session_token_provider=lambda: current[0],
        )
        self.assertTrue(authorize(admin.user_id, "agent:read", ""))
        self.assertTrue(authorize(admin.user_id, "agent:plan", ""))
        self.assertTrue(authorize(admin.user_id, "agent:dispatch", "plan-1"))
        self.assertTrue(authorize(admin.user_id, "agent:cancel", "plan-1"))
        self.assertTrue(authorize(admin.user_id, "agent:register", "research"))
        self.assertTrue(authorize(admin.user_id, "agent:manage", "research"))
        self.assertFalse(authorize("forged-user", "agent:read", ""))
        self.assertFalse(authorize(admin.user_id, "agent:unknown", ""))
        current[0] = "invalid-token"
        self.assertFalse(authorize(admin.user_id, "agent:read", ""))

    def test_agent_registry_mutations_require_admin_permission(self):
        from core.auth import agent_permission_authorizer
        user = self.auth.create_account("reader.user", "Another secure password 4!")
        session = self.auth.authenticate("reader.user", "Another secure password 4!")
        authorize = agent_permission_authorizer(
            self.auth, session_token_provider=lambda: session.token,
        )
        self.assertTrue(authorize(user.user_id, "agent:read", ""))
        self.assertFalse(authorize(user.user_id, "agent:plan", ""))
        self.assertFalse(authorize(user.user_id, "agent:register", "new-agent"))
        self.assertFalse(authorize(user.user_id, "agent:manage", "research"))

    def test_native_task_permission_authorizer_allows_only_owner_scoped_reads(self):
        from core.auth import native_task_permission_authorizer
        user = self.auth.create_account("native.user", "Another secure password 4!")
        session = self.auth.authenticate("native.user", "Another secure password 4!")
        current = [session.token]
        authorize = native_task_permission_authorizer(
            self.auth, session_token_provider=lambda: current[0],
        )
        self.assertTrue(authorize(user.user_id, "task:list"))
        self.assertTrue(authorize(user.user_id, "task:read"))
        self.assertFalse(authorize(user.user_id, "task:approve"))
        self.assertFalse(authorize(user.user_id, "memory:read"))
        self.assertFalse(authorize(user.user_id, "memory:write"))
        self.assertFalse(authorize("forged-user", "task:list"))
        current[0] = "invalid-token"
        self.assertFalse(authorize(user.user_id, "task:list"))


    def test_native_command_permission_authorizer_requires_consent_for_owner_writes(self):
        from core.auth import native_command_permission_authorizer
        user = self.auth.create_account("native.read", "Another secure password 5!")
        session = self.auth.authenticate("native.read", "Another secure password 5!")
        token = [session.token]
        consent = [False]
        authorize = native_command_permission_authorizer(
            self.auth,
            session_token_provider=lambda: token[0],
            memory_saving_enabled_provider=lambda: consent[0],
        )
        for capability in ("task:list", "task:read", "memory:read"):
            self.assertTrue(authorize(user.user_id, capability))
        self.assertFalse(authorize(user.user_id, "memory:write"))
        consent[0] = True
        self.assertTrue(authorize(user.user_id, "memory:write"))
        self.assertFalse(authorize("other-user", "memory:write"))
        consent[0] = False
        self.assertFalse(authorize(user.user_id, "memory:write"))
        for capability in ("task:create", "task:approve", "unknown"):
            self.assertFalse(authorize(user.user_id, capability))
        self.assertFalse(authorize("other-user", "memory:read"))
        token[0] = "revoked-or-invalid"
        consent[0] = True
        self.assertFalse(authorize(user.user_id, "memory:write"))


if __name__ == "__main__":
    unittest.main()
