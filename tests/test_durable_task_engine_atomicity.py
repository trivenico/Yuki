import sqlite3
import tempfile
import unittest
from pathlib import Path

from core.durable_task_engine import DurableTaskEngine


class DurableTaskEngineAtomicityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.now = [100.0]
        self.engine = DurableTaskEngine(
            Path(self.temp.name) / "tasks.sqlite3",
            clock=lambda: self.now[0],
        )
        self.goal_id = self.engine.create_goal()
        self.task = self.engine.submit(
            self.goal_id, "work", {"input": "safe"},
            idempotency_key="atomic-event",
        )
        self.claimed = self.engine.claim("worker-a", lease_seconds=30)

    def reject_event(self, event_type):
        with self.engine._connect() as db:
            # SQLite trigger definitions do not accept bound parameters.
            # event_type is a fixed test constant, not user-controlled input.
            db.execute(
                f"""
                CREATE TRIGGER reject_task_event
                BEFORE INSERT ON run_events
                WHEN NEW.event_type = '{event_type}'
                BEGIN SELECT RAISE(ABORT, 'simulated event journal failure'); END
                """
            )

    def test_completion_and_success_event_roll_back_together(self):
        self.reject_event("task.succeeded")

        with self.assertRaises(sqlite3.IntegrityError):
            self.engine.complete(
                self.task["id"], "worker-a", {"result": "must not persist"}
            )

        current = self.engine.get_task(self.task["id"])
        self.assertEqual(current["state"], "running")
        self.assertEqual(current["lease_owner"], "worker-a")
        self.assertIsNone(current["result"])
        self.assertEqual(
            [event["event_type"] for event in self.engine.list_events(self.goal_id)],
            ["goal.created", "task.queued", "task.claimed"],
        )

    def test_heartbeat_and_heartbeat_event_roll_back_together(self):
        original_lease = self.engine.get_task(self.task["id"])["lease_until"]
        self.reject_event("task.heartbeat")
        self.now[0] += 5

        with self.assertRaises(sqlite3.IntegrityError):
            self.engine.heartbeat(self.task["id"], "worker-a", lease_seconds=90)

        current = self.engine.get_task(self.task["id"])
        self.assertEqual(current["lease_until"], original_lease)
        self.assertEqual(
            [event["event_type"] for event in self.engine.list_events(self.goal_id)],
            ["goal.created", "task.queued", "task.claimed"],
        )


    def test_retry_scheduling_and_failure_event_roll_back_together(self):
        self.reject_event("task.retry_queued")
        original = self.engine.get_task(self.task["id"])

        with self.assertRaises(sqlite3.IntegrityError):
            self.engine.fail(
                self.task["id"], "worker-a", "temporary secret-bearing failure",
                retry_delay_seconds=45,
            )

        current = self.engine.get_task(self.task["id"])
        self.assertEqual(current["state"], "running")
        self.assertEqual(current["attempts"], original["attempts"])
        self.assertEqual(current["available_at"], original["available_at"])
        self.assertEqual(current["lease_owner"], "worker-a")
        self.assertEqual(current["lease_until"], original["lease_until"])
        self.assertIsNone(current["last_error"])
        self.assertEqual(
            [event["event_type"] for event in self.engine.list_events(self.goal_id)],
            ["goal.created", "task.queued", "task.claimed"],
        )

    def test_expired_lease_recovery_and_expiry_event_roll_back_together(self):
        task = self.engine.submit(
            self.goal_id, "expiring", {}, idempotency_key="expiry-atomic",
            max_attempts=1,
        )
        # Claim the newly queued task; this task has exhausted its only attempt
        # when the lease expires.
        claimed = self.engine.claim("worker-b", lease_seconds=5)
        self.assertEqual(claimed["id"], task["id"])
        self.now[0] += 6
        original = self.engine.get_task(task["id"])
        self.reject_event("task.lease_expired")

        with self.assertRaises(sqlite3.IntegrityError):
            self.engine.recover_expired_leases()

        current = self.engine.get_task(task["id"])
        self.assertEqual(current["state"], "running")
        self.assertEqual(current["lease_owner"], "worker-b")
        self.assertEqual(current["lease_until"], original["lease_until"])
        self.assertIsNone(current["last_error"])
        self.assertEqual(
            [event["event_type"] for event in self.engine.list_events(self.goal_id)],
            ["goal.created", "task.queued", "task.claimed", "task.queued", "task.claimed"],
        )


    def test_terminal_rejection_and_rejection_event_roll_back_together(self):
        self.reject_event("task.rejected")
        original = self.engine.get_task(self.task["id"])

        with self.assertRaises(sqlite3.IntegrityError):
            self.engine.reject(
                self.task["id"], "worker-a", "unsupported_task_type"
            )

        current = self.engine.get_task(self.task["id"])
        self.assertEqual(current["state"], "running")
        self.assertEqual(current["attempts"], original["attempts"])
        self.assertEqual(current["lease_owner"], "worker-a")
        self.assertEqual(current["lease_until"], original["lease_until"])
        self.assertIsNone(current["last_error"])
        self.assertEqual(
            [event["event_type"] for event in self.engine.list_events(self.goal_id)],
            ["goal.created", "task.queued", "task.claimed"],
        )

    def test_dependency_failure_propagation_and_events_roll_back_together(self):
        goal_id = self.engine.create_goal()
        parent = self.engine.submit(
            goal_id, "parent", {}, idempotency_key="parent-atomic",
            max_attempts=1,
        )
        child = self.engine.submit(
            goal_id, "child", {}, idempotency_key="child-atomic",
            dependencies=[parent["id"]],
        )
        claimed = self.engine.claim("dependency-worker", lease_seconds=30)
        self.assertEqual(claimed["id"], parent["id"])
        self.reject_event("task.blocked_dependency")

        with self.assertRaises(sqlite3.IntegrityError):
            self.engine.fail(parent["id"], "dependency-worker", "permanent failure")

        current_parent = self.engine.get_task(parent["id"])
        current_child = self.engine.get_task(child["id"])
        self.assertEqual(current_parent["state"], "running")
        self.assertEqual(current_parent["lease_owner"], "dependency-worker")
        self.assertEqual(current_parent["attempts"], 1)
        self.assertIsNone(current_parent["last_error"])
        self.assertEqual(current_child["state"], "queued")
        self.assertIsNone(current_child["last_error"])
        self.assertEqual(
            [event["event_type"] for event in self.engine.list_events(goal_id)],
            ["goal.created", "task.queued", "task.queued", "task.claimed"],
        )


    def test_cancellation_and_blocked_dependency_propagation_roll_back_together(self):
        goal_id = self.engine.create_goal()
        parent = self.engine.submit(
            goal_id, "parent", {}, idempotency_key="cancel-parent-atomic",
        )
        child = self.engine.submit(
            goal_id, "child", {}, idempotency_key="cancel-child-atomic",
            dependencies=[parent["id"]],
        )
        self.reject_event("task.blocked_dependency")

        with self.assertRaises(sqlite3.IntegrityError):
            self.engine.cancel_task(parent["id"])

        current_parent = self.engine.get_task(parent["id"])
        current_child = self.engine.get_task(child["id"])
        self.assertEqual(current_parent["state"], "queued")
        self.assertEqual(current_child["state"], "queued")
        self.assertIsNone(current_parent["lease_owner"])
        self.assertIsNone(current_child["last_error"])
        self.assertEqual(
            [event["event_type"] for event in self.engine.list_events(goal_id)],
            ["goal.created", "task.queued", "task.queued"],
        )


if __name__ == "__main__":
    unittest.main()
