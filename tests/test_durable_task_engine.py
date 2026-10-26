import tempfile
import unittest
from pathlib import Path

from core.durable_task_engine import DurableTaskEngine, TaskConflict, TaskNotFound


class DurableTaskEngineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "tasks.sqlite"
        self.engine = DurableTaskEngine(self.db)
        self.goal = self.engine.create_goal()

    def tearDown(self):
        self.tmp.cleanup()

    def test_task_survives_engine_restart_and_emits_events(self):
        task = self.engine.submit(self.goal, "summarize", {"text": "hello"}, idempotency_key="one")
        again = DurableTaskEngine(self.db)
        self.assertEqual(again.get_task(task["id"])["payload"], {"text": "hello"})
        self.assertEqual([e["event_type"] for e in again.list_events(self.goal)], ["goal.created", "task.queued"])

    def test_idempotency_returns_same_task_but_rejects_different_payload(self):
        a = self.engine.submit(self.goal, "summarize", {"v": 1}, idempotency_key="same")
        b = self.engine.submit(self.goal, "summarize", {"v": 1}, idempotency_key="same")
        self.assertEqual(a["id"], b["id"])
        with self.assertRaises(TaskConflict):
            self.engine.submit(self.goal, "summarize", {"v": 2}, idempotency_key="same")

    def test_dependencies_gate_claim_until_parent_succeeds(self):
        first = self.engine.submit(self.goal, "collect", {}, idempotency_key="a")
        second = self.engine.submit(self.goal, "analyze", {}, idempotency_key="b", dependencies=[first["id"]])
        self.assertEqual(self.engine.claim("worker")["id"], first["id"])
        self.assertIsNone(self.engine.claim("worker"))
        self.engine.complete(first["id"], "worker", {"ok": True})
        self.assertEqual(self.engine.claim("worker")["id"], second["id"])

    def test_failure_retries_only_to_configured_attempt_limit(self):
        task = self.engine.submit(self.goal, "transient", {}, idempotency_key="retry", max_attempts=2)
        claimed = self.engine.claim("worker")
        self.assertEqual(claimed["id"], task["id"])
        self.assertEqual(self.engine.fail(task["id"], "worker", "temporary")["state"], "queued")
        claimed = self.engine.claim("worker")
        self.assertEqual(claimed["attempts"], 2)
        self.assertEqual(self.engine.fail(task["id"], "worker", "permanent")["state"], "failed")

    def test_expired_lease_recovery_and_stale_worker_rejected(self):
        now = [100.0]
        engine = DurableTaskEngine(self.db, clock=lambda: now[0])
        goal = engine.create_goal()
        task = engine.submit(goal, "work", {}, idempotency_key="lease", max_attempts=2)
        engine.claim("worker-a", lease_seconds=5)
        now[0] += 6
        self.assertEqual(engine.recover_expired_leases(), 1)
        with self.assertRaises(TaskConflict):
            engine.complete(task["id"], "worker-a", {})
        self.assertEqual(engine.claim("worker-b")["id"], task["id"])

    def test_pause_cancel_and_reject_new_work_when_goal_not_active(self):
        self.engine.set_goal_state(self.goal, "paused")
        with self.assertRaises(TaskConflict):
            self.engine.submit(self.goal, "work", {}, idempotency_key="paused")
        self.engine.set_goal_state(self.goal, "cancelled")
        with self.assertRaises(TaskConflict):
            self.engine.submit(self.goal, "work", {}, idempotency_key="cancelled")

    def test_payload_size_is_bounded(self):
        with self.assertRaises(ValueError):
            self.engine.submit(self.goal, "work", {"text": "x" * 1_000_100}, idempotency_key="large")

    def test_unknown_goal_and_task_are_clear_errors(self):
        with self.assertRaises(TaskNotFound):
            self.engine.get_task("missing")
        with self.assertRaises(TaskNotFound):
            self.engine.submit("missing", "work", {}, idempotency_key="x")


if __name__ == "__main__":
    unittest.main()
