import sqlite3
import tempfile
import threading
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

    def test_idempotency_canonicalizes_payload_key_order(self):
        first = self.engine.submit(
            self.goal, "work", {"a": 1, "b": 2}, idempotency_key="canonical"
        )
        replay = self.engine.submit(
            self.goal, "work", {"b": 2, "a": 1}, idempotency_key="canonical"
        )
        self.assertEqual(first["id"], replay["id"])

    def test_idempotency_rejects_changed_execution_options(self):
        parent = self.engine.submit(self.goal, "parent", {}, idempotency_key="parent")
        self.engine.submit(self.goal, "child", {"v": 1}, idempotency_key="child", dependencies=[parent["id"]], max_attempts=2, delay_seconds=3)
        for kwargs in (
            {"dependencies": [], "max_attempts": 2, "delay_seconds": 3},
            {"dependencies": [parent["id"]], "max_attempts": 3, "delay_seconds": 3},
            {"dependencies": [parent["id"]], "max_attempts": 2, "delay_seconds": 4},
        ):
            with self.assertRaises(TaskConflict):
                self.engine.submit(self.goal, "child", {"v": 1}, idempotency_key="child", **kwargs)

    def test_dependencies_reject_strings_and_malformed_ids(self):
        with self.assertRaises(ValueError):
            self.engine.submit(self.goal, "work", {}, idempotency_key="string-deps", dependencies="task-id")
        with self.assertRaises(ValueError):
            self.engine.submit(self.goal, "work", {}, idempotency_key="bad-dep", dependencies=[None])

    def test_worker_error_details_are_not_persisted(self):
        task = self.engine.submit(self.goal, "work", {}, idempotency_key="redact", max_attempts=1)
        self.engine.claim("worker")
        secret = "api_key=very-secret-value"
        self.engine.fail(task["id"], "worker", secret)
        self.assertNotIn("very-secret-value", self.engine.get_task(task["id"])["last_error"])
        events = self.engine.list_events(self.goal)
        self.assertNotIn("very-secret-value", str(events))

    def test_invalid_lease_and_delay_numbers_are_rejected(self):
        for invalid_delay in (float("nan"), float("inf"), True):
            with self.subTest(delay=invalid_delay):
                with self.assertRaises(ValueError):
                    self.engine.submit(
                        self.goal, "work", {},
                        idempotency_key=f"invalid-delay-{str(invalid_delay)}",
                        delay_seconds=invalid_delay,
                    )
        with self.assertRaises(ValueError):
            self.engine.claim("worker", lease_seconds=True)
        with self.assertRaises(ValueError):
            self.engine.heartbeat("missing", "worker", lease_seconds=True)

        task = self.engine.submit(
            self.goal, "work", {}, idempotency_key="invalid-retry-delay"
        )
        self.engine.claim("worker")
        with self.assertRaises(ValueError):
            self.engine.fail(
                task["id"], "worker", "error", retry_delay_seconds=float("inf")
            )

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

    def test_failed_dependency_marks_descendant_failed_instead_of_stranding_it(self):
        parent = self.engine.submit(self.goal, "parent", {}, idempotency_key="parent", max_attempts=1)
        child = self.engine.submit(self.goal, "child", {}, idempotency_key="child", dependencies=[parent["id"]])
        self.engine.claim("worker")
        self.engine.fail(parent["id"], "worker", "permanent failure")
        self.assertEqual(self.engine.get_task(child["id"])["state"], "failed")
        self.assertIsNone(self.engine.claim("worker"))

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

    def test_concurrent_workers_claim_distinct_tasks(self):
        first = self.engine.submit(self.goal, "work", {"n": 1}, idempotency_key="concurrent-1")
        second = self.engine.submit(self.goal, "work", {"n": 2}, idempotency_key="concurrent-2")
        barrier = threading.Barrier(2)
        claimed = []
        errors = []

        def claim(worker):
            try:
                barrier.wait(timeout=5)
                claimed.append(self.engine.claim(worker)["id"])
            except Exception as exc:
                errors.append(exc)

        workers = [
            threading.Thread(target=claim, args=("worker-a",)),
            threading.Thread(target=claim, args=("worker-b",)),
        ]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=10)
        self.assertFalse(any(worker.is_alive() for worker in workers), "claim workers timed out")
        self.assertEqual(errors, [])
        self.assertEqual(len(claimed), 2)
        self.assertEqual(set(claimed), {first["id"], second["id"]})

    def test_existing_database_gets_fingerprint_migration(self):
        # Simulate the pre-fingerprint schema; opening the engine must migrate
        # additively rather than requiring a destructive database rebuild.
        legacy_db = Path(self.tmp.name) / "legacy.sqlite"
        conn = sqlite3.connect(legacy_db)
        try:
            conn.executescript("""
                CREATE TABLE goals(
                    id TEXT PRIMARY KEY, state TEXT NOT NULL,
                    created_at REAL NOT NULL, updated_at REAL NOT NULL
                );
                CREATE TABLE tasks(
                    id TEXT PRIMARY KEY, goal_id TEXT NOT NULL,
                    task_type TEXT NOT NULL, payload_json TEXT NOT NULL, result_json TEXT,
                    state TEXT NOT NULL, idempotency_key TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0, max_attempts INTEGER NOT NULL,
                    available_at REAL NOT NULL, lease_owner TEXT, lease_until REAL,
                    last_error TEXT, created_at REAL NOT NULL, updated_at REAL NOT NULL,
                    UNIQUE(goal_id,idempotency_key)
                );
            """)
        finally:
            conn.close()
        migrated = DurableTaskEngine(legacy_db)
        conn = sqlite3.connect(legacy_db)
        try:
            columns = {row[1] for row in conn.execute("PRAGMA table_info(tasks)")}
        finally:
            conn.close()
        self.assertIn("submission_fingerprint", columns)
        goal = migrated.create_goal("migrated-goal")
        task = migrated.submit(goal, "work", {}, idempotency_key="new-task")
        self.assertIsNotNone(task["submission_fingerprint"])

        # A pre-migration row has no verifiable fingerprint and must not be
        # silently accepted as an idempotent replay with unknown options.
        conn = sqlite3.connect(legacy_db)
        try:
            conn.execute(
                """INSERT INTO tasks(
                    id, goal_id, task_type, payload_json, state, idempotency_key,
                    attempts, max_attempts, available_at, created_at, updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                ("legacy-task", goal, "work", "{}", "queued", "legacy-key", 0, 3, 0, 0, 0),
            )
            conn.commit()
        finally:
            conn.close()
        with self.assertRaises(TaskConflict):
            migrated.submit(goal, "work", {}, idempotency_key="legacy-key")

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
