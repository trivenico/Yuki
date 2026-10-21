"""Security regression tests for approval coordinator behavior."""
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace

from core.approval_audit import SQLiteApprovalAuditLog
from core.approval_flow import ApprovalCoordinator
from core.pending_approvals import SQLitePendingApprovalStore


class DummyPlanner:
    def __init__(self):
        self.executed = []

    def execute_approved_action(self, action):
        self.executed.append(action)
        return SimpleNamespace(ok=True, result="ok")


class ApprovalCoordinatorSecurityTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        store = SQLitePendingApprovalStore(str(Path(self.tempdir.name) / "pending.sqlite3"))
        audit = SQLiteApprovalAuditLog(str(Path(self.tempdir.name) / "audit.sqlite3"))
        self.coordinator = ApprovalCoordinator(pending_store=store, audit_log=audit)
        self.planner = DummyPlanner()
        self.action = {"skill_id": "notes.write", "arguments": {"text": "safe"}}
        result = SimpleNamespace(action=self.action)
        self.outcome = self.coordinator.record(
            self.planner, result, actor="owner", session_id="session-1"
        )

    def test_confirmation_callback_cannot_mutate_executed_snapshot(self):
        def mutate_for_display(display):
            display["arguments"]["text"] = "tampered"
            return True

        result = self.coordinator.approve(
            self.planner, self.outcome.pending_id, actor="owner",
            session_id="session-1", confirm=mutate_for_display,
        )
        self.assertTrue(result.ok)
        self.assertEqual(self.planner.executed[0]["arguments"]["text"], "safe")

    def test_confirmation_exception_fails_closed_and_consumes_request(self):
        def broken(_):
            raise RuntimeError("UI unavailable")

        result = self.coordinator.approve(
            self.planner, self.outcome.pending_id, actor="owner",
            session_id="session-1", confirm=broken,
        )
        self.assertEqual(result.status, "approval_denied")
        self.assertEqual(self.planner.executed, [])
        replay = self.coordinator.approve(
            self.planner, self.outcome.pending_id, actor="owner",
            session_id="session-1", confirm=lambda _: True,
        )
        self.assertEqual(replay.status, "pending_not_found")
        self.assertEqual(self.planner.executed, [])

    def test_approval_is_actor_and_session_bound(self):
        wrong_actor = self.coordinator.approve(
            self.planner, self.outcome.pending_id, actor="intruder",
            session_id="session-1", confirm=lambda _: True,
        )
        self.assertEqual(wrong_actor.status, "pending_not_found")
        correct = self.coordinator.approve(
            self.planner, self.outcome.pending_id, actor="owner",
            session_id="session-1", confirm=lambda _: True,
        )
        self.assertTrue(correct.ok)
        self.assertEqual(len(self.planner.executed), 1)

    def test_two_concurrent_approvals_execute_at_most_once(self):
        barrier = threading.Barrier(3)
        outcomes = []
        lock = threading.Lock()

        def attempt():
            barrier.wait()
            result = self.coordinator.approve(
                self.planner, self.outcome.pending_id, actor="owner",
                session_id="session-1", confirm=lambda _: True,
            )
            with lock:
                outcomes.append(result.status)

        threads = [threading.Thread(target=attempt) for _ in range(2)]
        for thread in threads:
            thread.start()
        barrier.wait()
        for thread in threads:
            thread.join(timeout=5)
        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(len(self.planner.executed), 1)
        self.assertEqual(outcomes.count("completed"), 1)
        self.assertEqual(outcomes.count("pending_not_found"), 1)


if __name__ == "__main__":
    unittest.main()
