"""Unit and persistence tests for the guarded approval coordinator.

These tests deliberately exercise only modules present in this branch. End-to-end
skill execution belongs in a separate integration test once the manifest runtime
and subprocess adapter are implemented on the same branch.
"""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from core.approval_audit import SQLiteApprovalAuditLog
from core.approval_flow import ApprovalCoordinator
from core.pending_approvals import SQLitePendingApprovalStore


class FakePlanner:
    def __init__(self):
        self.executed = []

    def execute_approved_action(self, action):
        self.executed.append(action)
        return SimpleNamespace(ok=True, value="executed")


class MemoryAudit:
    def __init__(self):
        self.events = []

    def append(self, **event):
        self.events.append(event)


def action_result(text="safe note"):
    return {"action": {"skill_id": "notes.write", "arguments": {"text": text}}}


class ApprovalCoordinatorTests(unittest.TestCase):
    def setUp(self):
        self.planner = FakePlanner()
        self.pending_store = SQLitePendingApprovalStore(str(Path(tempfile.mkdtemp()) / "pending.sqlite3"))
        self.coordinator = ApprovalCoordinator(pending_store=self.pending_store, audit_log=MemoryAudit())
        self.pending = self.coordinator.record(
            self.planner, action_result(), actor="owner", session_id="session-a"
        )

    def approve(self, coordinator=None, pending_id=None, actor="owner", session_id="session-a",
                confirm=lambda _action: True):
        return (coordinator or self.coordinator).approve(
            self.planner, pending_id or self.pending.pending_id, actor=actor,
            session_id=session_id, confirm=confirm,
        )

    def test_missing_audit_sink_blocks_before_creating_pending_request(self):
        with self.assertRaisesRegex(ValueError, "durable pending-approval store is required"):
            ApprovalCoordinator(audit_log=MemoryAudit())
        self.assertEqual(self.planner.executed, [])

    def test_missing_audit_sink_blocks_approval_execution(self):
        with self.assertRaisesRegex(ValueError, "durable pending-approval store is required"):
            ApprovalCoordinator()
        self.assertEqual(self.planner.executed, [])

    def test_audit_failure_does_not_leave_pending_request_available(self):
        class BrokenAudit:
            def append(self, **_event):
                raise OSError("audit unavailable")

        with tempfile.TemporaryDirectory() as tmp:
            store = SQLitePendingApprovalStore(str(Path(tmp) / "pending.sqlite3"))
            coordinator = ApprovalCoordinator(pending_store=store, audit_log=BrokenAudit())
            with self.assertRaisesRegex(RuntimeError, "could not be audited"):
                coordinator.record(
                    self.planner, action_result(), actor="owner", session_id="session-a"
                )
            with store._connect() as db:
                count = db.execute("SELECT COUNT(*) FROM pending_approvals").fetchone()[0]
            self.assertEqual(count, 0)
            self.assertEqual(self.planner.executed, [])

    def test_persistent_pending_request_is_removed_when_audit_fails(self):
        class BrokenAudit:
            def append(self, **_event):
                raise OSError("audit unavailable")

        with tempfile.TemporaryDirectory() as tmp:
            store = SQLitePendingApprovalStore(str(Path(tmp) / "pending.sqlite3"))
            coordinator = ApprovalCoordinator(pending_store=store, audit_log=BrokenAudit())
            with self.assertRaisesRegex(RuntimeError, "could not be audited"):
                coordinator.record(
                    self.planner, action_result(), actor="owner", session_id="session-a"
                )
            with store._connect() as db:
                count = db.execute("SELECT COUNT(*) FROM pending_approvals").fetchone()[0]
            self.assertEqual(count, 0)
            self.assertEqual(self.planner.executed, [])

    def test_approval_audit_failure_prevents_execution(self):
        class SelectiveAudit:
            def append(self, *, event_type, **_event):
                if event_type == "approved":
                    raise OSError("audit unavailable")

        coordinator = ApprovalCoordinator(pending_store=self.pending_store, audit_log=SelectiveAudit())
        pending = coordinator.record(
            self.planner, action_result(), actor="owner", session_id="session-a"
        )
        outcome = coordinator.approve(
            self.planner, pending.pending_id, actor="owner", session_id="session-a",
            confirm=lambda _action: True,
        )
        self.assertEqual(outcome.status, "audit_unavailable")
        self.assertEqual(self.planner.executed, [])

    def test_execution_audit_failure_returns_recovery_required_not_retryable_failure(self):
        class SelectiveAudit:
            def append(self, *, event_type, **_event):
                if event_type == "executed":
                    raise OSError("audit unavailable")

        coordinator = ApprovalCoordinator(pending_store=self.pending_store, audit_log=SelectiveAudit())
        pending = coordinator.record(
            self.planner, action_result(), actor="owner", session_id="session-a"
        )
        outcome = coordinator.approve(
            self.planner, pending.pending_id, actor="owner", session_id="session-a",
            confirm=lambda _action: True,
        )
        self.assertEqual(outcome.status, "recovery_required")
        self.assertEqual(len(self.planner.executed), 1)

    def test_record_requires_explicit_approval_before_execution(self):
        self.assertEqual(self.pending.status, "pending_approval")
        self.assertFalse(self.pending.ok)
        self.assertEqual(self.planner.executed, [])

    def test_approval_executes_the_stored_action_once(self):
        outcome = self.approve()
        self.assertTrue(outcome.ok)
        self.assertEqual(outcome.status, "completed")
        self.assertEqual(self.planner.executed[0]["arguments"], {"text": "safe note"})
        replay = self.approve()
        self.assertEqual(replay.status, "pending_not_found")
        self.assertEqual(len(self.planner.executed), 1)

    def test_denial_does_not_execute_and_consumes_the_request(self):
        denied = self.approve(confirm=lambda _action: False)
        self.assertEqual(denied.status, "approval_denied")
        self.assertEqual(self.planner.executed, [])
        self.assertEqual(self.approve().status, "pending_not_found")

    def test_returned_failure_result_is_not_reported_as_completed(self):
        class FailedPlanner:
            def execute_approved_action(self, action):
                return SimpleNamespace(ok=False, error="policy rejected")

        pending_store = self.pending_store
        coordinator = ApprovalCoordinator(
            pending_store=pending_store,
            audit_log=MemoryAudit(),
        )
        pending = coordinator.record(
            self.planner, action_result("failure"), actor="owner", session_id="session-a"
        )
        outcome = coordinator.approve(
            FailedPlanner(), pending.pending_id, actor="owner", session_id="session-a",
            confirm=lambda _action: True,
        )
        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.status, "execution_failed")
        self.assertEqual(outcome.result.error, "policy rejected")

    def test_dict_failure_result_is_not_reported_as_completed(self):
        class DictFailurePlanner:
            def execute_approved_action(self, action):
                return {"ok": False, "error": "adapter rejected"}

        coordinator = ApprovalCoordinator(
            pending_store=self.pending_store,
            audit_log=MemoryAudit(),
        )
        pending = coordinator.record(
            self.planner, action_result("dict-failure"), actor="owner", session_id="session-a"
        )
        outcome = coordinator.approve(
            DictFailurePlanner(), pending.pending_id, actor="owner", session_id="session-a",
            confirm=lambda _action: True,
        )
        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.status, "execution_failed")
        self.assertEqual(outcome.result["error"], "adapter rejected")

    def test_returned_failure_is_audited_as_execution_failed(self):
        class FailedPlanner:
            def execute_approved_action(self, action):
                return SimpleNamespace(ok=False)

        class RecordingAudit:
            def __init__(self):
                self.events = []
            def append(self, **event):
                self.events.append(event)

        audit = RecordingAudit()
        coordinator = ApprovalCoordinator(pending_store=self.pending_store, audit_log=audit)
        pending = coordinator.record(
            self.planner, action_result("failure"), actor="owner", session_id="session-a"
        )
        outcome = coordinator.approve(
            FailedPlanner(), pending.pending_id, actor="owner", session_id="session-a",
            confirm=lambda _action: True,
        )
        self.assertEqual(outcome.status, "execution_failed")
        self.assertEqual([event["event_type"] for event in audit.events],
                         ["requested", "approved", "execution_failed"])

    def test_execution_ledger_records_success_before_completed_outcome(self):
        from core.execution_ledger import SQLiteExecutionLedger

        with tempfile.TemporaryDirectory() as tmp:
            ledger = SQLiteExecutionLedger(str(Path(tmp) / "ledger.sqlite3"))
            store = SQLitePendingApprovalStore(str(Path(tmp) / "pending.sqlite3"))
            audit = MemoryAudit()
            coordinator = ApprovalCoordinator(
                pending_store=store, audit_log=audit, execution_ledger=ledger
            )
            pending = coordinator.record(
                self.planner, action_result("ledger-success"),
                actor="owner", session_id="session-a",
            )
            outcome = coordinator.approve(
                self.planner, pending.pending_id, actor="owner",
                session_id="session-a", confirm=lambda _action: True,
            )
            self.assertTrue(outcome.ok)
            self.assertEqual(outcome.status, "completed")
            record = ledger.get(pending.pending_id)
            self.assertEqual(record.state, "completed")

    def test_existing_prepared_ledger_record_fails_closed_without_execution(self):
        from core.execution_ledger import SQLiteExecutionLedger

        with tempfile.TemporaryDirectory() as tmp:
            ledger = SQLiteExecutionLedger(str(Path(tmp) / "ledger.sqlite3"))
            store = SQLitePendingApprovalStore(str(Path(tmp) / "pending.sqlite3"))
            audit = MemoryAudit()
            planner = FakePlanner()
            coordinator = ApprovalCoordinator(
                pending_store=store, audit_log=audit, execution_ledger=ledger
            )
            pending = coordinator.record(
                planner, action_result("prepared-replay"),
                actor="owner", session_id="session-a",
            )
            ledger.begin(pending.pending_id, pending.result)

            outcome = coordinator.approve(
                planner, pending.pending_id, actor="owner",
                session_id="session-a", confirm=lambda _action: True,
            )

            self.assertEqual(outcome.status, "recovery_required")
            self.assertEqual(outcome.result["execution_state"], "prepared")
            self.assertEqual(planner.executed, [])
            self.assertIn(
                "execution_recovery_required",
                [event["event_type"] for event in audit.events],
            )

    def test_execution_ledger_marks_raised_execution_uncertain_and_does_not_retry(self):
        from core.execution_ledger import SQLiteExecutionLedger

        class CrashingPlanner:
            def execute_approved_action(self, action):
                raise TimeoutError("remote result unknown")

        with tempfile.TemporaryDirectory() as tmp:
            ledger = SQLiteExecutionLedger(str(Path(tmp) / "ledger.sqlite3"))
            store = SQLitePendingApprovalStore(str(Path(tmp) / "pending.sqlite3"))
            coordinator = ApprovalCoordinator(
                pending_store=store, audit_log=MemoryAudit(), execution_ledger=ledger
            )
            pending = coordinator.record(
                self.planner, action_result("ledger-uncertain"),
                actor="owner", session_id="session-a",
            )
            outcome = coordinator.approve(
                CrashingPlanner(), pending.pending_id, actor="owner",
                session_id="session-a", confirm=lambda _action: True,
            )
            self.assertEqual(outcome.status, "recovery_required")
            self.assertEqual(ledger.get(pending.pending_id).state, "uncertain")
            self.assertEqual(
                [r.idempotency_key for r in ledger.list_reconciliation_required()],
                [pending.pending_id],
            )

    def test_confirmation_callback_cannot_mutate_executed_snapshot(self):
        def mutate(display_action):
            display_action["arguments"]["text"] = "tampered"
            return True
        outcome = self.approve(confirm=mutate)
        self.assertTrue(outcome.ok)
        self.assertEqual(self.planner.executed[0]["arguments"]["text"], "safe note")

    def test_approval_is_bound_to_actor_and_session(self):
        self.assertEqual(self.approve(actor="intruder").status, "pending_not_found")
        self.assertEqual(self.approve(session_id="session-b").status, "pending_not_found")
        self.assertTrue(self.approve().ok)

    def test_persistent_store_and_audit_log_survive_coordinator_recreation(self):
        with tempfile.TemporaryDirectory() as tmp:
            pending_store = SQLitePendingApprovalStore(str(Path(tmp) / "pending.sqlite3"))
            audit_log = SQLiteApprovalAuditLog(str(Path(tmp) / "audit.sqlite3"))
            coordinator = ApprovalCoordinator(pending_store=pending_store, audit_log=audit_log)
            pending = coordinator.record(
                self.planner, action_result("persisted"), actor="owner", session_id="session-a"
            )
            outcome = coordinator.approve(
                self.planner, pending.pending_id, actor="owner", session_id="session-a",
                confirm=lambda shown: shown["arguments"]["text"] == "persisted",
            )
            self.assertTrue(outcome.ok)
            events = list(reversed(audit_log.list_events()))
            self.assertEqual([event["event_type"] for event in events],
                             ["requested", "approved", "executed"])
            self.assertNotIn("persisted", str(events))


if __name__ == "__main__":
    unittest.main()
