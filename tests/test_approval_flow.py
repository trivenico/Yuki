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

        coordinator = ApprovalCoordinator(pending_store=self.pending_store, audit_log=BrokenAudit())
        with self.assertRaisesRegex(RuntimeError, "could not be audited"):
            coordinator.record(
                self.planner, action_result(), actor="owner", session_id="session-a"
            )
        with self.pending_store._connect() as db:
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
