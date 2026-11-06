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


if __name__ == "__main__":
    unittest.main()
