"""End-to-end tests for owner approval through one-shot guarded dispatch."""
import json
from pathlib import Path
import tempfile
import unittest

from core.approval_audit import SQLiteApprovalAuditLog
from core.approval_flow import ApprovalCoordinator
from core.auth import BuiltInAuthService, guarded_runtime_identity_validator
from core.guarded_approval_executor import GuardedApprovalExecutor
from core.tool_action_approvals import SQLiteToolApprovalStore


class GuardedApprovalExecutorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.auth = BuiltInAuthService(
            str(root / "auth.sqlite3"), password_iterations=100_000
        )
        self.auth.bootstrap_admin("owner@example.test", "correct horse battery staple")
        self.session = self.auth.authenticate(
            "owner@example.test", "correct horse battery staple"
        )
        self.calls = []
        self.audit = SQLiteApprovalAuditLog(str(root / "audit.sqlite3"))
        self.store = SQLiteToolApprovalStore(str(root / "tool-approvals.sqlite3"))
        self.validator = guarded_runtime_identity_validator(
            self.auth, session_token_provider=lambda: self.session.token
        )
        self.executor = GuardedApprovalExecutor(
            {"notes.write": self.write_note},
            approval_store=self.store,
            audit_log=self.audit,
            identity_validator=self.validator,
            target_scopes={"notes.write": "owner-notes"},
        )
        self.coordinator = ApprovalCoordinator(
            guarded_executor=self.executor, audit_log=self.audit
        )

    def tearDown(self):
        self.tmp.cleanup()

    def write_note(self, argument):
        payload = json.loads(argument)
        self.calls.append(payload["text"])
        return "saved"

    def proposal(self, skill_id="notes.write", text="approved text"):
        return {"action": {"skill_id": skill_id, "arguments": {"text": text}}}

    def approve(self, proposal=None):
        pending = self.coordinator.record(
            object(), proposal or self.proposal(),
            actor=self.session.user.user_id, session_id=self.session.token,
        )
        return self.coordinator.approve(
            object(), pending.pending_id,
            actor=self.session.user.user_id, session_id=self.session.token,
            confirm=lambda _shown: True,
        )

    def test_owner_confirmation_executes_exact_registered_action_once(self):
        outcome = self.approve()
        self.assertTrue(outcome.ok)
        self.assertEqual(outcome.status, "completed")
        self.assertEqual(self.calls, ["approved text"])
        events = [item["event_type"] for item in reversed(self.audit.list_events())]
        self.assertEqual(
            events, ["requested", "approved", "approved", "executed"]
        )

    def test_unregistered_skill_never_reaches_a_handler(self):
        outcome = self.approve(self.proposal("notes.delete"))
        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.status, "execution_failed")
        self.assertEqual(self.calls, [])

    def test_session_revoked_during_dispatch_fails_closed(self):
        validations = []
        def revoke_between_checks(owner, session):
            validations.append(True)
            if len(validations) == 1:
                return self.validator(owner, session)
            self.auth.revoke_session(session)
            return self.validator(owner, session)

        executor = GuardedApprovalExecutor(
            {"notes.write": self.write_note},
            approval_store=self.store,
            audit_log=self.audit,
            identity_validator=revoke_between_checks,
            target_scopes={"notes.write": "owner-notes"},
        )
        coordinator = ApprovalCoordinator(
            guarded_executor=executor, audit_log=self.audit
        )
        pending = coordinator.record(
            object(), self.proposal(),
            actor=self.session.user.user_id, session_id=self.session.token,
        )
        outcome = coordinator.approve(
            object(), pending.pending_id,
            actor=self.session.user.user_id, session_id=self.session.token,
            confirm=lambda _shown: True,
        )
        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.status, "denied")
        self.assertEqual(self.calls, [])

    def test_executor_requires_a_scope_for_every_registered_handler(self):
        with self.assertRaisesRegex(ValueError, "exactly one target scope"):
            GuardedApprovalExecutor(
                {"notes.write": self.write_note},
                approval_store=self.store,
                audit_log=self.audit,
                identity_validator=self.validator,
                target_scopes={},
            )


if __name__ == "__main__":
    unittest.main()
