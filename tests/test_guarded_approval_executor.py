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

    def test_all_approval_stores_share_one_fence_end_to_end(self):
        from core.maintenance_fence import MaintenanceFence
        from core.pending_approvals import SQLitePendingApprovalStore

        root = Path(self.tmp.name) / "fenced"
        root.mkdir(mode=0o700)
        fence = MaintenanceFence(root)
        audit = SQLiteApprovalAuditLog(
            str(root / "audit.sqlite3"), maintenance_fence=fence,
        )
        pending_store = SQLitePendingApprovalStore(
            str(root / "pending.sqlite3"), maintenance_fence=fence,
        )
        grant_store = SQLiteToolApprovalStore(
            str(root / "grants.sqlite3"), maintenance_fence=fence,
        )
        executor = GuardedApprovalExecutor(
            {"notes.write": self.write_note},
            approval_store=grant_store,
            audit_log=audit,
            identity_validator=self.validator,
            target_scopes={"notes.write": "owner-notes"},
        )
        coordinator = ApprovalCoordinator(
            pending_store=pending_store, audit_log=audit,
            guarded_executor=executor, maintenance_fence=fence,
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
        self.assertTrue(outcome.ok)
        self.assertEqual(self.calls, ["approved text"])
        self.assertTrue(audit.verify_chain())
        self.assertTrue((root / "maintenance.lock").is_file())

    def test_guarded_executor_rejects_different_grant_and_audit_fences(self):
        from core.maintenance_fence import MaintenanceFence

        root = Path(self.tmp.name)
        first = MaintenanceFence(root / "first")
        second = MaintenanceFence(root / "second")
        grant_store = SQLiteToolApprovalStore(
            str(root / "mismatch-grants.sqlite3"), maintenance_fence=first,
        )
        audit = SQLiteApprovalAuditLog(
            str(root / "mismatch-audit.sqlite3"), maintenance_fence=second,
        )
        with self.assertRaisesRegex(ValueError, "share one maintenance fence"):
            GuardedApprovalExecutor(
                {"notes.write": self.write_note},
                approval_store=grant_store,
                audit_log=audit,
                identity_validator=self.validator,
                target_scopes={"notes.write": "owner-notes"},
            )

    def test_owner_confirmation_executes_exact_registered_action_once(self):
        outcome = self.approve()
        self.assertTrue(outcome.ok)
        self.assertEqual(outcome.status, "completed")
        self.assertEqual(self.calls, ["approved text"])
        events = [item["event_type"] for item in reversed(self.audit.list_events())]
        self.assertEqual(
            events, ["requested", "approved", "approved", "execution_started", "executed"]
        )

    def test_unregistered_skill_never_reaches_a_handler(self):
        outcome = self.approve(self.proposal("notes.delete"))
        self.assertFalse(outcome.ok)
        # The coordinator has durably marked the dispatch boundary before
        # calling the adapter; an adapter exception is conservatively uncertain.
        self.assertEqual(outcome.status, "outcome_unknown")
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

    def test_handler_exception_is_reported_as_uncertain_not_failed(self):
        calls = []
        def side_effect_then_raise(argument):
            calls.append("side-effect")
            raise RuntimeError("connection lost after remote write")

        executor = GuardedApprovalExecutor(
            {"notes.write": side_effect_then_raise},
            approval_store=self.store, audit_log=self.audit,
            identity_validator=self.validator,
            target_scopes={"notes.write": "owner-notes"},
        )
        coordinator = ApprovalCoordinator(guarded_executor=executor, audit_log=self.audit)
        pending = coordinator.record(
            object(), self.proposal(), actor=self.session.user.user_id,
            session_id=self.session.token,
        )
        outcome = coordinator.approve(
            object(), pending.pending_id, actor=self.session.user.user_id,
            session_id=self.session.token, confirm=lambda _shown: True,
        )

        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.status, "outcome_unknown")
        events = [item["event_type"] for item in reversed(self.audit.list_events())]
        self.assertEqual(events[-2:], ["execution_started", "outcome_unknown"])
        self.assertEqual(calls, ["side-effect"])
        retry = coordinator.approve(
            object(), pending.pending_id, actor=self.session.user.user_id,
            session_id=self.session.token, confirm=lambda _shown: True,
        )
        self.assertEqual(retry.status, "pending_not_found")

    def test_policy_audit_failure_keeps_consumed_grant_unusable(self):
        original_append = self.audit.append
        approved_appends = []

        def fail_second_approved_append(**kwargs):
            if kwargs.get("event_type") == "approved":
                approved_appends.append(True)
                if len(approved_appends) == 2:
                    raise OSError("simulated durable policy-audit failure")
            return original_append(**kwargs)

        self.audit.append = fail_second_approved_append
        pending = self.coordinator.record(
            object(), self.proposal(), actor=self.session.user.user_id,
            session_id=self.session.token,
        )
        outcome = self.coordinator.approve(
            object(), pending.pending_id, actor=self.session.user.user_id,
            session_id=self.session.token, confirm=lambda _shown: True,
        )

        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.status, "execution_blocked")
        self.assertEqual(self.calls, [])
        self.assertEqual(self.store.list_recovery_metadata(), [])
        events = [item["event_type"] for item in reversed(self.audit.list_events())]
        self.assertEqual(events[-2:], ["execution_started", "execution_blocked"])

        retry = self.coordinator.approve(
            object(), pending.pending_id, actor=self.session.user.user_id,
            session_id=self.session.token, confirm=lambda _shown: True,
        )
        self.assertEqual(retry.status, "pending_not_found")
        self.assertEqual(self.calls, [])
        self.assertEqual(self.store.list_recovery_metadata(), [])

    def test_executor_requires_a_scope_for_every_registered_handler(self):
        with self.assertRaisesRegex(ValueError, "exactly one target scope"):
            GuardedApprovalExecutor(
                {"notes.write": self.write_note},
                approval_store=self.store,
                audit_log=self.audit,
                identity_validator=self.validator,
                target_scopes={},
            )

    def test_audit_recovery_listing_finds_only_unresolved_dispatches(self):
        self.audit.append(
            event_type="execution_started", pending_id="completed-operation",
            actor="owner-a", skill_id="notes.write",
        )
        self.audit.append(
            event_type="executed", pending_id="completed-operation",
            actor="owner-a", skill_id="notes.write",
        )
        self.audit.append(
            event_type="execution_started", pending_id="interrupted-operation",
            actor="owner-b", skill_id="notes.delete",
        )

        unresolved = self.audit.list_unresolved_executions()
        self.assertEqual(len(unresolved), 1)
        item = unresolved[0]
        self.assertEqual(item["skill_id"], "notes.delete")
        self.assertNotIn("interrupted-operation", repr(item))
        self.assertNotIn("owner-b", repr(item))
        self.assertEqual(len(item["pending_id_hash"]), 64)
        self.assertEqual(len(item["actor_hash"]), 64)

    def test_owner_reconciliation_records_resolution_without_dispatch(self):
        started_id = self.audit.append(
            event_type="execution_started", pending_id="interrupted-operation",
            actor=self.session.user.user_id, skill_id="notes.write",
        )
        self.audit.append(
            event_type="outcome_unknown", pending_id="interrupted-operation",
            actor=self.session.user.user_id, skill_id="notes.write",
        )
        before_calls = list(self.calls)
        self.assertEqual(len(self.audit.list_unresolved_executions()), 1)
        evidence = "ticket:INC-4821 verified target record"
        event_id = self.audit.reconcile_unresolved_execution(
            started_id, actor=self.session.user.user_id,
            session_id=self.session.token, identity_validator=self.validator,
            resolution="executed", evidence=evidence,
        )
        self.assertGreater(event_id, started_id)
        self.assertEqual(self.calls, before_calls)
        self.assertEqual(self.audit.list_unresolved_executions(), [])
        events = [item["event_type"] for item in reversed(self.audit.list_events())]
        self.assertEqual(events[-1], "reconciled_executed")
        reconciliation = self.audit.list_events(limit=1)[0]
        self.assertEqual(
            reconciliation["skill_id"],
            "evidence:" + __import__("hashlib").sha256(evidence.encode()).hexdigest(),
        )
        self.assertNotIn(evidence, repr(reconciliation))
        self.assertTrue(self.audit.verify_chain())

    def test_owner_reconciliation_rejects_wrong_owner_and_invalid_resolution(self):
        started_id = self.audit.append(
            event_type="execution_started", pending_id="interrupted-operation",
            actor=self.session.user.user_id, skill_id="notes.write",
        )
        with self.assertRaises(PermissionError):
            self.audit.reconcile_unresolved_execution(
                started_id, actor="different-owner", session_id=self.session.token,
                identity_validator=lambda _owner, _session: True,
                resolution="executed", evidence="verified by owner",
            )
        with self.assertRaises(ValueError):
            self.audit.reconcile_unresolved_execution(
                started_id, actor=self.session.user.user_id,
                session_id=self.session.token, identity_validator=self.validator,
                resolution="retry", evidence="verified by owner",
            )
        self.assertEqual(len(self.audit.list_unresolved_executions()), 1)

    def test_owner_reconciliation_rechecks_live_session_before_append(self):
        started_id = self.audit.append(
            event_type="execution_started", pending_id="interrupted-operation",
            actor=self.session.user.user_id, skill_id="notes.write",
        )
        validations = []

        def revoke_between_checks(actor, session_id):
            validations.append(True)
            return len(validations) == 1 and self.validator(actor, session_id)

        with self.assertRaises(PermissionError):
            self.audit.reconcile_unresolved_execution(
                started_id, actor=self.session.user.user_id,
                session_id=self.session.token, identity_validator=revoke_between_checks,
                resolution="not_executed", evidence="independent target inspection",
            )
        self.assertEqual(len(validations), 2)
        self.assertEqual(len(self.audit.list_unresolved_executions()), 1)

    def test_audit_recovery_listing_refuses_invalid_chain(self):
        import sqlite3
        self.audit.append(
            event_type="execution_started", pending_id="interrupted-operation",
            actor="owner", skill_id="notes.write",
        )
        with sqlite3.connect(self.audit.database_path) as db:
            db.execute(
                "UPDATE approval_audit_events SET event_hash=? WHERE event_id=1",
                ("0" * 64,),
            )
        with self.assertRaisesRegex(RuntimeError, "chain is invalid"):
            self.audit.list_unresolved_executions()


if __name__ == "__main__":
    unittest.main()
