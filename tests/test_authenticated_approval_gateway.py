"""Tests for session-authenticated approval request and confirmation."""
import tempfile
import unittest
from pathlib import Path

from core.approval_audit import SQLiteApprovalAuditLog
from core.approval_flow import ApprovalCoordinator
from core.auth import BuiltInAuthService, guarded_runtime_identity_validator
from core.authenticated_approval_gateway import AuthenticatedApprovalGateway
from core.pending_approvals import SQLitePendingApprovalStore


class Planner:
    def __init__(self):
        self.executed = []

    def execute_approved_action(self, action):
        self.executed.append(action)
        return type("Result", (), {"ok": True})()


def proposal():
    return {"action": {"skill_id": "notes.write", "arguments": {"text": "hello"}}}


class AuthenticatedApprovalGatewayTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.auth = BuiltInAuthService(
            str(root / "auth.sqlite3"), password_iterations=100_000
        )
        self.auth.bootstrap_admin("owner@example.test", "correct horse battery staple")
        self.admin_session = self.auth.authenticate(
            "owner@example.test", "correct horse battery staple"
        )
        self.token = self.admin_session.token
        self.planner = Planner()
        self.pending = SQLitePendingApprovalStore(str(root / "pending.sqlite3"))
        self.audit = SQLiteApprovalAuditLog(str(root / "audit.sqlite3"))
        self.coordinator = ApprovalCoordinator(
            guarded_executor=lambda planner, action, actor, session_id: planner.execute_approved_action(action),
            pending_store=self.pending, audit_log=self.audit
        )
        self.gateway = AuthenticatedApprovalGateway(
            self.auth, self.coordinator, session_token_provider=lambda: self.token
        )

    def tearDown(self):
        self.tmp.cleanup()

    def test_authenticated_owner_can_request_and_confirm(self):
        requested = self.gateway.request(self.planner, proposal())
        self.assertEqual(requested.status, "pending_approval")
        self.assertEqual(self.planner.executed, [])
        confirmed = self.gateway.confirm(
            self.planner, requested.pending_id, confirm=lambda shown: (
                shown["skill_id"] == "notes.write"
                and shown["arguments"]["text"] == "hello"
            ),
        )
        self.assertEqual(confirmed.status, "completed")
        self.assertEqual(len(self.planner.executed), 1)

    def test_missing_or_revoked_session_cannot_request_or_confirm(self):
        self.token = ""
        self.assertEqual(
            self.gateway.request(self.planner, proposal()).status,
            "authentication_required",
        )
        self.token = self.admin_session.token
        requested = self.gateway.request(self.planner, proposal())
        self.auth.revoke_session(self.token)
        self.assertEqual(
            self.gateway.confirm(
                self.planner, requested.pending_id, confirm=lambda _shown: True
            ).status,
            "authentication_required",
        )
        self.assertEqual(self.planner.executed, [])

    def test_least_privileged_user_cannot_request_or_confirm(self):
        self.auth.create_account("reader@example.test", "reader password long enough")
        reader_session = self.auth.authenticate(
            "reader@example.test", "reader password long enough"
        )
        self.token = reader_session.token
        self.assertEqual(
            self.gateway.request(self.planner, proposal()).status,
            "permission_denied",
        )
        self.assertEqual(
            self.gateway.confirm(self.planner, "unknown", confirm=lambda _: True).status,
            "permission_denied",
        )
        self.assertEqual(self.planner.executed, [])

    def test_guarded_runtime_validator_rechecks_live_session_and_owner(self):
        validator = guarded_runtime_identity_validator(
            self.auth, session_token_provider=lambda: self.token
        )
        self.assertTrue(validator(self.admin_session.user.user_id, self.token))
        self.assertFalse(validator("different-owner", self.token))
        self.auth.revoke_session(self.token)
        self.assertFalse(validator(self.admin_session.user.user_id, self.token))

    def test_pending_action_is_bound_to_creator_session(self):
        requested = self.gateway.request(self.planner, proposal())
        other_session = self.auth.authenticate(
            "owner@example.test", "correct horse battery staple"
        )
        self.token = other_session.token
        result = self.gateway.confirm(
            self.planner, requested.pending_id, confirm=lambda _: True
        )
        self.assertEqual(result.status, "pending_not_found")
        self.assertEqual(self.planner.executed, [])

    def test_confirmation_callback_is_required_and_denial_consumes_request(self):
        requested = self.gateway.request(self.planner, proposal())
        self.assertEqual(
            self.gateway.confirm(self.planner, requested.pending_id, confirm=None).status,
            "confirmation_unavailable",
        )
        denied = self.gateway.confirm(
            self.planner, requested.pending_id, confirm=lambda _: False
        )
        self.assertEqual(denied.status, "approval_denied")
        self.assertEqual(self.planner.executed, [])


if __name__ == "__main__":
    unittest.main()
