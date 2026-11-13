"""Security regression tests for approval coordinator behavior."""
import sqlite3
import tempfile
from contextlib import closing
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace

from core.approval_flow import ApprovalCoordinator
from core.pending_approvals import SQLitePendingApprovalStore


class MemoryAudit:
    def append(self, **_event):
        pass


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
        self.coordinator = ApprovalCoordinator(pending_store=store, audit_log=MemoryAudit(), planner_resolver=lambda **_: self.planner)
        self.planner = DummyPlanner()
        self.action = {"skill_id": "notes.write", "arguments": {"text": "safe"}}
        result = SimpleNamespace(action=self.action)
        self.outcome = self.coordinator.record(
            self.planner, result, actor="owner", session_id="session-1"
        )

    def test_invalid_identity_cannot_claim_or_consume_approval(self):
        invalid = self.coordinator.approve(
            self.planner, self.outcome.pending_id, actor=" ",
            session_id="session-1", confirm=lambda _: True,
        )
        self.assertEqual(invalid.status, "pending_not_found")
        valid = self.coordinator.approve(
            self.planner, self.outcome.pending_id, actor="owner",
            session_id="session-1", confirm=lambda _: True,
        )
        self.assertTrue(valid.ok)
        self.assertEqual(len(self.planner.executed), 1)

    def test_missing_confirmation_callback_fails_without_consuming_approval(self):
        invalid = self.coordinator.approve(
            self.planner, self.outcome.pending_id, actor="owner",
            session_id="session-1", confirm=None,
        )
        self.assertEqual(invalid.status, "approval_confirmation_unavailable")
        valid = self.coordinator.approve(
            self.planner, self.outcome.pending_id, actor="owner",
            session_id="session-1", confirm=lambda _: True,
        )
        self.assertTrue(valid.ok)
        self.assertEqual(len(self.planner.executed), 1)

    def test_host_resolver_prevents_request_planner_substitution(self):
        trusted = DummyPlanner()
        attacker = DummyPlanner()
        coordinator = ApprovalCoordinator(
            pending_store=self.coordinator.pending_store, audit_log=MemoryAudit(),
            planner_resolver=lambda *, actor, session_id: trusted
                if (actor, session_id) == ("owner", "session-1") else None,
        )
        result = coordinator.record(
            trusted, SimpleNamespace(action=self.action),
            actor="owner", session_id="session-1",
        )
        outcome = coordinator.approve(
            attacker, result.pending_id, actor="owner", session_id="session-1",
            confirm=lambda _: True,
        )
        self.assertTrue(outcome.ok)
        self.assertEqual(len(trusted.executed), 1)
        self.assertEqual(attacker.executed, [])

    def test_host_resolver_failure_prevents_execution(self):
        coordinator = ApprovalCoordinator(
            pending_store=self.coordinator.pending_store, audit_log=MemoryAudit(),
            planner_resolver=lambda **_: None,
        )
        result = coordinator.record(
            self.planner, SimpleNamespace(action=self.action),
            actor="owner", session_id="session-1",
        )
        outcome = coordinator.approve(
            self.planner, result.pending_id, actor="owner", session_id="session-1",
            confirm=lambda _: True,
        )
        self.assertEqual(outcome.status, "execution_unavailable")
        self.assertEqual(self.planner.executed, [])

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

    def test_interrupted_confirmation_can_be_confirmed_again(self):
        store = self.coordinator.pending_store
        claimed = store.transition(
            self.outcome.pending_id, expected_status="pending",
            new_status="claimed", actor="owner", session_id="session-1",
        )
        self.assertIsNotNone(claimed)
        resumed = self.coordinator.approve(
            self.planner, self.outcome.pending_id, actor="owner",
            session_id="session-1", confirm=lambda _: True,
        )
        self.assertEqual(resumed.status, "completed")
        self.assertEqual(len(self.planner.executed), 1)

    def test_unknown_execution_outcome_survives_pending_ttl(self):
        store = self.coordinator.pending_store
        pending_id = self.outcome.pending_id
        for expected, new in (
            ("pending", "claimed"),
            ("claimed", "approved"),
            ("approved", "executing"),
            ("executing", "outcome_unknown"),
        ):
            self.assertIsNotNone(store.transition(
                pending_id, expected_status=expected, new_status=new,
                actor="owner", session_id="session-1",
            ))
        with closing(sqlite3.connect(store.database_path)) as db:
            with db:
                db.execute(
                    "UPDATE pending_approvals SET expires_at=? WHERE pending_id=?",
                    ("2000-01-01T00:00:00+00:00", pending_id),
                )
        retained = store.get(pending_id)
        self.assertIsNotNone(retained)
        self.assertEqual(retained.status, "outcome_unknown")

    def test_completed_approval_is_recognized_as_non_replayable(self):
        first = self.coordinator.approve(
            self.planner, self.outcome.pending_id, actor="owner",
            session_id="session-1", confirm=lambda _: True,
        )
        self.assertEqual(first.status, "completed")
        replay = self.coordinator.approve(
            self.planner, self.outcome.pending_id, actor="owner",
            session_id="session-1", confirm=lambda _: True,
        )
        self.assertEqual(replay.status, "recovery_required")
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
        self.assertEqual(outcomes.count("recovery_required"), 1)


if __name__ == "__main__":
    unittest.main()
