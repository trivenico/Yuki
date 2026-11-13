"""Unit and persistence tests for the guarded approval coordinator.

These tests deliberately exercise only modules present in this branch. End-to-end
skill execution belongs in a separate integration test once the manifest runtime
and subprocess adapter are implemented on the same branch.
"""
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace

from core.approval_audit import SQLiteApprovalAuditLog
from core.approval_flow import ApprovalCoordinator
from core.pending_approvals import SQLitePendingApprovalStore


class MemoryAudit:
    def append(self, **_event):
        pass


class FakePlanner:
    def __init__(self):
        self.executed = []

    def execute_approved_action(self, action):
        self.executed.append(action)
        return SimpleNamespace(ok=True, value="executed")


def action_result(text="safe note"):
    return {"action": {"skill_id": "notes.write", "arguments": {"text": text}}}


class ApprovalCoordinatorTests(unittest.TestCase):
    def setUp(self):
        self.planner = FakePlanner()
        self.coordinator = ApprovalCoordinator(audit_log=MemoryAudit(), planner_resolver=lambda **_: self.planner)
        self.pending = self.coordinator.record(
            self.planner, action_result(), actor="owner", session_id="session-a"
        )

    def approve(self, coordinator=None, pending_id=None, actor="owner", session_id="session-a",
                confirm=lambda _action: True):
        return (coordinator or self.coordinator).approve(
            self.planner, pending_id or self.pending.pending_id, actor=actor,
            session_id=session_id, confirm=confirm,
        )

    def test_audit_log_and_trusted_executor_resolver_are_mandatory(self):
        with self.assertRaisesRegex(ValueError, "required approval audit log"):
            ApprovalCoordinator()
        with self.assertRaisesRegex(ValueError, "trusted host planner_resolver"):
            ApprovalCoordinator(audit_log=MemoryAudit())

    def test_audit_failure_does_not_leave_pending_request_available(self):
        class BrokenAudit:
            def append(self, **_event):
                raise OSError("audit unavailable")

        coordinator = ApprovalCoordinator(audit_log=BrokenAudit(), planner_resolver=lambda **_: self.planner)
        with self.assertRaisesRegex(RuntimeError, "could not be audited"):
            coordinator.record(
                self.planner, action_result(), actor="owner", session_id="session-a"
            )
        self.assertEqual(coordinator._pending, {})
        self.assertEqual(self.planner.executed, [])

    def test_persistent_pending_request_is_removed_when_audit_fails(self):
        class BrokenAudit:
            def append(self, **_event):
                raise OSError("audit unavailable")

        with tempfile.TemporaryDirectory() as tmp:
            store = SQLitePendingApprovalStore(str(Path(tmp) / "pending.sqlite3"))
            coordinator = ApprovalCoordinator(pending_store=store, audit_log=BrokenAudit(), planner_resolver=lambda **_: self.planner)
            with self.assertRaisesRegex(RuntimeError, "could not be audited"):
                coordinator.record(
                    self.planner, action_result(), actor="owner", session_id="session-a"
                )
            with store._connect() as db:
                rows = db.execute("SELECT status FROM pending_approvals").fetchall()
            self.assertEqual(rows, [("denied",)])
            self.assertEqual(self.planner.executed, [])

    def test_approval_audit_failure_prevents_execution(self):
        class SelectiveAudit:
            def append(self, *, event_type, **_event):
                if event_type == "approved":
                    raise OSError("audit unavailable")

        coordinator = ApprovalCoordinator(audit_log=SelectiveAudit(), planner_resolver=lambda **_: self.planner)
        pending = coordinator.record(
            self.planner, action_result(), actor="owner", session_id="session-a"
        )
        outcome = coordinator.approve(
            self.planner, pending.pending_id, actor="owner", session_id="session-a",
            confirm=lambda _action: True,
        )
        self.assertEqual(outcome.status, "audit_unavailable")
        self.assertEqual(self.planner.executed, [])

    def test_executor_exception_is_unknown_outcome_and_never_reported_retryable(self):
        events = []

        class RecordingAudit:
            def append(self, *, event_type, **_event):
                events.append(event_type)

        class AmbiguousExecutor:
            def execute_approved_action(self, action):
                self.side_effects += 1
                raise TimeoutError("provider response was lost")

            def __init__(self):
                self.side_effects = 0

        executor = AmbiguousExecutor()
        coordinator = ApprovalCoordinator(
            audit_log=RecordingAudit(),
            planner_resolver=lambda **_: executor,
        )
        pending = coordinator.record(
            self.planner, action_result(), actor="owner", session_id="session-a"
        )
        outcome = coordinator.approve(
            self.planner, pending.pending_id, actor="owner", session_id="session-a",
            confirm=lambda _action: True,
        )

        self.assertEqual(outcome.status, "recovery_required")
        self.assertEqual(outcome.result["outcome"], "unknown")
        self.assertEqual(executor.side_effects, 1)
        self.assertEqual(events, [
            "requested", "approved", "execution_started", "outcome_unknown",
        ])
        self.assertEqual(
            coordinator.approve(
                self.planner, pending.pending_id, actor="owner", session_id="session-a",
                confirm=lambda _action: True,
            ).status,
            "pending_not_found",
        )
        self.assertEqual(executor.side_effects, 1)

    def test_execution_audit_failure_returns_recovery_required_not_retryable_failure(self):
        class SelectiveAudit:
            def append(self, *, event_type, **_event):
                if event_type == "executed":
                    raise OSError("audit unavailable")

        coordinator = ApprovalCoordinator(audit_log=SelectiveAudit(), planner_resolver=lambda **_: self.planner)
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
            coordinator = ApprovalCoordinator(pending_store=pending_store, audit_log=audit_log, planner_resolver=lambda **_: self.planner)
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
                             ["requested", "approved", "execution_started", "executed"])
            self.assertNotIn("persisted", str(events))


    def test_concurrent_in_memory_approvals_execute_at_most_once(self):
        barrier = threading.Barrier(3)
        outcomes = []
        outcomes_lock = threading.Lock()

        def attempt():
            barrier.wait()
            outcome = self.approve()
            with outcomes_lock:
                outcomes.append(outcome.status)

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

    def test_rejects_oversized_action_snapshot(self):
        with self.assertRaisesRegex(ValueError, "JSON-safe"):
            self.coordinator.record(
                self.planner, action_result("x" * (17 * 1024)),
                actor="owner", session_id="session-a",
            )

    def test_rejects_unbounded_identity_values(self):
        with self.assertRaisesRegex(ValueError, "actor identity"):
            self.coordinator.record(
                self.planner, action_result(), actor="x" * 257, session_id="session-a"
            )
        with self.assertRaisesRegex(ValueError, "session identity"):
            self.coordinator.record(
                self.planner, action_result(), actor="owner", session_id="x" * 257
            )


    def test_persistent_store_transitions_are_identity_bound_and_restart_safe(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "pending.sqlite3")
            store = SQLitePendingApprovalStore(path)
            item = store.put(
                "notes.write", {"text": "durable"},
                {"skill_id": "notes.write", "arguments": {"text": "durable"}},
                actor="owner", session_id="session-a",
            )
            self.assertEqual(item.status, "pending")
            self.assertIsNone(store.transition(
                item.pending_id, expected_status="pending", new_status="claimed",
                actor="intruder", session_id="session-a",
            ))
            self.assertIsNone(store.transition(
                item.pending_id, expected_status="pending", new_status="claimed",
                actor="owner", session_id="session-b",
            ))
            claimed = store.transition(
                item.pending_id, expected_status="pending", new_status="claimed",
                actor="owner", session_id="session-a",
            )
            self.assertEqual(claimed.status, "claimed")
            self.assertEqual(claimed.action["arguments"]["text"], "durable")
            self.assertIsNone(store.transition(
                item.pending_id, expected_status="pending", new_status="claimed",
                actor="owner", session_id="session-a",
            ))

            restarted = SQLitePendingApprovalStore(path)
            persisted = restarted.get(item.pending_id)
            self.assertEqual(persisted.status, "claimed")
            approved = restarted.transition(
                item.pending_id, expected_status="claimed", new_status="approved",
                actor="owner", session_id="session-a",
            )
            self.assertEqual(approved.status, "approved")
            executing = restarted.transition(
                item.pending_id, expected_status="approved", new_status="executing",
                actor="owner", session_id="session-a",
            )
            self.assertEqual(executing.status, "executing")
            completed = restarted.transition(
                item.pending_id, expected_status="executing", new_status="completed",
                actor="owner", session_id="session-a",
            )
            self.assertEqual(completed.status, "completed")
            self.assertEqual(restarted.get(item.pending_id).action["arguments"]["text"], "durable")

    def test_persistent_store_rejects_invalid_and_terminal_state_transitions(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = SQLitePendingApprovalStore(str(Path(tmp) / "pending.sqlite3"))
            item = store.put(
                "notes.write", {}, {"skill_id": "notes.write", "arguments": {}},
                actor="owner", session_id="session-a",
            )
            self.assertIsNone(store.transition(
                item.pending_id, expected_status="pending", new_status="completed",
                actor="owner", session_id="session-a",
            ))
            claimed = store.transition(
                item.pending_id, expected_status="pending", new_status="claimed",
                actor="owner", session_id="session-a",
            )
            self.assertEqual(store.transition(
                item.pending_id, expected_status="claimed", new_status="approved",
                actor="owner", session_id="session-a",
            ).status, "approved")
            self.assertEqual(store.transition(
                item.pending_id, expected_status="approved", new_status="executing",
                actor="owner", session_id="session-a",
            ).status, "executing")
            unknown = store.transition(
                item.pending_id, expected_status="executing", new_status="outcome_unknown",
                actor="owner", session_id="session-a",
            )
            self.assertEqual(unknown.status, "outcome_unknown")
            self.assertIsNone(store.transition(
                item.pending_id, expected_status="outcome_unknown", new_status="executing",
                actor="owner", session_id="session-a",
            ))


    def test_persistent_approved_action_resumes_without_repeating_confirmation(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = SQLitePendingApprovalStore(str(Path(tmp) / "pending.sqlite3"))
            item = store.put(
                "notes.write", {"text": "resume"},
                {"skill_id": "notes.write", "arguments": {"text": "resume"}},
                actor="owner", session_id="session-a",
            )
            claimed = store.transition(
                item.pending_id, expected_status="pending", new_status="claimed",
                actor="owner", session_id="session-a",
            )
            self.assertEqual(claimed.status, "claimed")
            approved = store.transition(
                item.pending_id, expected_status="claimed", new_status="approved",
                actor="owner", session_id="session-a",
            )
            self.assertEqual(approved.status, "approved")

            confirmation_calls = []
            coordinator = ApprovalCoordinator(
                pending_store=SQLitePendingApprovalStore(str(Path(tmp) / "pending.sqlite3")),
                audit_log=MemoryAudit(), planner_resolver=lambda **_: self.planner,
            )
            outcome = coordinator.approve(
                self.planner, item.pending_id, actor="owner", session_id="session-a",
                confirm=lambda _action: confirmation_calls.append(True) or False,
            )
            self.assertEqual(outcome.status, "completed")
            self.assertEqual(confirmation_calls, [])
            self.assertEqual(len(self.planner.executed), 1)
            self.assertEqual(store.get(item.pending_id).status, "completed")

    def test_persistent_execution_claim_is_never_replayed_after_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "pending.sqlite3")
            store = SQLitePendingApprovalStore(path)
            item = store.put(
                "notes.write", {"text": "uncertain"},
                {"skill_id": "notes.write", "arguments": {"text": "uncertain"}},
                actor="owner", session_id="session-a",
            )
            store.transition(
                item.pending_id, expected_status="pending", new_status="claimed",
                actor="owner", session_id="session-a",
            )
            store.transition(
                item.pending_id, expected_status="claimed", new_status="approved",
                actor="owner", session_id="session-a",
            )
            store.transition(
                item.pending_id, expected_status="approved", new_status="executing",
                actor="owner", session_id="session-a",
            )
            coordinator = ApprovalCoordinator(
                pending_store=SQLitePendingApprovalStore(path),
                audit_log=MemoryAudit(), planner_resolver=lambda **_: self.planner,
            )
            confirmations = []
            outcome = coordinator.approve(
                self.planner, item.pending_id, actor="owner", session_id="session-a",
                confirm=lambda _action: confirmations.append(True) or True,
            )
            self.assertEqual(outcome.status, "recovery_required")
            self.assertEqual(outcome.result["outcome"], "unknown")
            self.assertEqual(confirmations, [])
            self.assertEqual(self.planner.executed, [])
            self.assertEqual(store.get(item.pending_id).status, "executing")


    def test_persistent_concurrent_approvals_across_coordinators_execute_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "pending.sqlite3")
            store = SQLitePendingApprovalStore(path)
            item = store.put(
                "notes.write", {"text": "single"},
                {"skill_id": "notes.write", "arguments": {"text": "single"}},
                actor="owner", session_id="session-a",
            )
            coordinators = [
                ApprovalCoordinator(
                    pending_store=SQLitePendingApprovalStore(path),
                    audit_log=MemoryAudit(), planner_resolver=lambda **_: self.planner,
                )
                for _ in range(2)
            ]
            barrier = threading.Barrier(3)
            outcomes = []
            lock = threading.Lock()

            def attempt(coordinator):
                barrier.wait()
                outcome = coordinator.approve(
                    self.planner, item.pending_id, actor="owner", session_id="session-a",
                    confirm=lambda _action: True,
                )
                with lock:
                    outcomes.append(outcome.status)

            threads = [threading.Thread(target=attempt, args=(coordinator,))
                       for coordinator in coordinators]
            for thread in threads:
                thread.start()
            barrier.wait()
            for thread in threads:
                thread.join(timeout=5)
            self.assertTrue(all(not thread.is_alive() for thread in threads))
            self.assertEqual(len(self.planner.executed), 1)
            self.assertEqual(outcomes.count("completed"), 1)
            self.assertEqual(len(outcomes), 2)
            self.assertEqual(sum(outcome != "completed" for outcome in outcomes), 1)
            self.assertEqual(store.get(item.pending_id).status, "completed")


    def test_expired_ttl_does_not_delete_claimed_execution_recovery_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "pending.sqlite3")
            store = SQLitePendingApprovalStore(path)
            item = store.put(
                "notes.write", {}, {"skill_id": "notes.write", "arguments": {}},
                actor="owner", session_id="session-a",
            )
            claimed = store.transition(
                item.pending_id, expected_status="pending", new_status="claimed",
                actor="owner", session_id="session-a",
            )
            self.assertEqual(claimed.status, "claimed")
            with store._connect() as db:
                db.execute(
                    "UPDATE pending_approvals SET expires_at=? WHERE pending_id=?",
                    ("2000-01-01T00:00:00+00:00", item.pending_id),
                )
            self.assertEqual(store.get(item.pending_id).status, "claimed")
            approved = store.transition(
                item.pending_id, expected_status="claimed", new_status="approved",
                actor="owner", session_id="session-a",
            )
            self.assertEqual(approved.status, "approved")
            executing = store.transition(
                item.pending_id, expected_status="approved", new_status="executing",
                actor="owner", session_id="session-a",
            )
            self.assertEqual(executing.status, "executing")


    def test_outcome_unknown_is_retained_after_ttl_and_capacity_cleanup(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "pending.sqlite3")
            store = SQLitePendingApprovalStore(path)
            item = store.put(
                "notes.write", {}, {"skill_id": "notes.write", "arguments": {}},
                actor="owner", session_id="session-a",
            )
            store.transition(
                item.pending_id, expected_status="pending", new_status="claimed",
                actor="owner", session_id="session-a",
            )
            store.transition(
                item.pending_id, expected_status="claimed", new_status="approved",
                actor="owner", session_id="session-a",
            )
            store.transition(
                item.pending_id, expected_status="approved", new_status="executing",
                actor="owner", session_id="session-a",
            )
            store.transition(
                item.pending_id, expected_status="executing", new_status="outcome_unknown",
                actor="owner", session_id="session-a",
            )
            with store._connect() as db:
                db.execute(
                    "UPDATE pending_approvals SET expires_at=? WHERE pending_id=?",
                    ("2000-01-01T00:00:00+00:00", item.pending_id),
                )
            self.assertEqual(store.get(item.pending_id).status, "outcome_unknown")
            new_item = store.put(
                "notes.write", {}, {"skill_id": "notes.write", "arguments": {}},
                actor="owner", session_id="session-a",
            )
            self.assertIsNotNone(store.get(item.pending_id))
            self.assertEqual(store.get(new_item.pending_id).status, "pending")

    def test_denial_requires_matching_owner_and_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = SQLitePendingApprovalStore(str(Path(tmp) / "pending.sqlite3"))
            item = store.put(
                "notes.write", {}, {"skill_id": "notes.write", "arguments": {}},
                actor="owner", session_id="session-a",
            )
            self.assertFalse(store.deny(item.pending_id, actor="intruder", session_id="session-a"))
            self.assertFalse(store.deny(item.pending_id, actor="owner", session_id="session-b"))
            self.assertEqual(store.get(item.pending_id).status, "pending")
            self.assertTrue(store.deny(item.pending_id, actor="owner", session_id="session-a"))
            self.assertEqual(store.get(item.pending_id).status, "denied")


    def test_unavailable_executor_is_persisted_and_can_resume_after_recovery(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = SQLitePendingApprovalStore(str(Path(tmp) / "pending.sqlite3"))
            resolvers = [None, self.planner]
            coordinator = ApprovalCoordinator(
                pending_store=store,
                audit_log=MemoryAudit(),
                planner_resolver=lambda **_: resolvers.pop(0),
            )
            pending = coordinator.record(
                self.planner, action_result(), actor="owner", session_id="session-a"
            )
            first = coordinator.approve(
                self.planner, pending.pending_id, actor="owner", session_id="session-a",
                confirm=lambda _action: True,
            )
            self.assertEqual(first.status, "execution_unavailable")
            self.assertEqual(store.get(pending.pending_id).status, "execution_unavailable")
            second = coordinator.approve(
                self.planner, pending.pending_id, actor="owner", session_id="session-a",
                confirm=lambda _action: self.fail("approval confirmation must not repeat"),
            )
            self.assertEqual(second.status, "completed")
            self.assertEqual(len(self.planner.executed), 1)
            self.assertEqual(store.get(pending.pending_id).status, "completed")


if __name__ == "__main__":
    unittest.main()
