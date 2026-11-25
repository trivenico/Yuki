import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path

from core.durable_task_engine import DurableTaskEngine, TaskConflict, TaskNotFound


def _claim(engine, worker_id, **kwargs):
    return engine.claim(worker_id, **kwargs)


def _complete(engine, claimed, result=None):
    return engine.complete(
        claimed["id"], claimed["lease_owner"], result,
        lease_generation=claimed["lease_generation"],
    )


def _fail(engine, claimed, error, **kwargs):
    return engine.fail(
        claimed["id"], claimed["lease_owner"], error,
        lease_generation=claimed["lease_generation"], **kwargs,
    )


class DurableTaskEngineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "tasks.sqlite"
        self.engine = DurableTaskEngine(self.db)
        self.goal = self.engine.create_goal()

    def tearDown(self):
        self.tmp.cleanup()

    def test_engine_close_is_idempotent_and_blocks_future_operations(self):
        engine = DurableTaskEngine(":memory:")
        goal = engine.create_goal()
        engine.close()
        engine.close()
        with self.assertRaises(RuntimeError):
            engine.get_goal(goal)

    def test_engine_context_manager_closes_resources(self):
        with DurableTaskEngine(":memory:") as engine:
            goal = engine.create_goal()
            self.assertEqual(engine.get_goal(goal)["state"], "active")
        with self.assertRaises(RuntimeError):
            engine.get_goal(goal)

    def test_in_memory_database_persists_across_engine_connections(self):
        engine = DurableTaskEngine(":memory:")
        goal = engine.create_goal()
        task = engine.submit(
            goal, "work", {"ok": True}, idempotency_key="memory-task"
        )
        claimed = engine.claim("memory-worker")
        self.assertEqual(claimed["id"], task["id"])
        completed = _complete(engine, claimed, {"done": True})
        self.assertEqual(completed["state"], "succeeded")
        self.assertEqual(engine.get_task(task["id"])["result"], {"done": True})

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

    def test_idempotent_replay_after_goal_completion_returns_original_task(self):
        task = self.engine.submit(
            self.goal, "work", {"value": 1}, idempotency_key="terminal-replay"
        )
        claimed = _claim(self.engine, "terminal-replay-worker")
        _complete(self.engine, claimed, {"done": True})
        self.assertEqual(self.engine.get_goal(self.goal)["state"], "completed")
        replay = self.engine.submit(
            self.goal, "work", {"value": 1}, idempotency_key="terminal-replay"
        )
        self.assertEqual(replay["id"], task["id"])
        with self.assertRaises(TaskConflict):
            self.engine.submit(
                self.goal, "work", {"value": 2}, idempotency_key="terminal-replay"
            )

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
            {"dependencies": [parent["id"]], "max_attempts": 2, "delay_seconds": 3, "retry_safe": True},
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
        claimed = _claim(self.engine, "worker")
        secret = "api_key=very-secret-value"
        _fail(self.engine, claimed, secret)
        self.assertNotIn("very-secret-value", self.engine.get_task(task["id"])["last_error"])
        events = self.engine.list_events(self.goal)
        self.assertNotIn("very-secret-value", str(events))

    def test_invalid_lease_and_delay_numbers_are_rejected(self):
        for index, invalid_delay in enumerate((float("nan"), float("inf"), True, 10 ** 1000)):
            with self.subTest(delay_type=type(invalid_delay).__name__, index=index):
                with self.assertRaises(ValueError):
                    self.engine.submit(
                        self.goal, "work", {},
                        idempotency_key=f"invalid-delay-{index}",
                        delay_seconds=invalid_delay,
                    )
        with self.assertRaises(ValueError):
            self.engine.claim("worker", lease_seconds=True)
        with self.assertRaises(ValueError):
            self.engine.heartbeat("missing", "worker", lease_generation="invalid", lease_seconds=True)

        task = self.engine.submit(
            self.goal, "work", {}, idempotency_key="invalid-retry-delay"
        )
        claimed = _claim(self.engine, "worker")
        for invalid_retry_delay in (float("inf"), float("nan"), 10 ** 1000, True):
            with self.subTest(retry_delay_type=type(invalid_retry_delay).__name__):
                with self.assertRaises(ValueError):
                    self.engine.fail(
                        task["id"], "worker", "error",
                        lease_generation=claimed["lease_generation"],
                        retry_delay_seconds=invalid_retry_delay,
                    )

    def test_dependencies_gate_claim_until_parent_succeeds(self):
        first = self.engine.submit(self.goal, "collect", {}, idempotency_key="a")
        second = self.engine.submit(self.goal, "analyze", {}, idempotency_key="b", dependencies=[first["id"]])
        claimed = _claim(self.engine, "worker")
        self.assertEqual(claimed["id"], first["id"])
        self.assertIsNone(self.engine.claim("worker"))
        _complete(self.engine, claimed, {"ok": True})
        self.assertEqual(_claim(self.engine, "worker")["id"], second["id"])

    def test_worker_failure_is_terminal_without_explicit_retry_safety(self):
        task = self.engine.submit(
            self.goal, "side-effect", {}, idempotency_key="failure-no-replay",
            max_attempts=3,
        )
        claimed = _claim(self.engine, "worker")
        failed = _fail(self.engine, claimed, "ambiguous outcome")
        self.assertEqual(failed["state"], "failed")
        self.assertIsNone(self.engine.claim("worker-next"))

    def test_failure_retries_only_to_configured_attempt_limit(self):
        task = self.engine.submit(self.goal, "transient", {}, idempotency_key="retry", max_attempts=2, retry_safe=True)
        claimed = self.engine.claim("worker")
        self.assertEqual(claimed["id"], task["id"])
        self.assertEqual(_fail(self.engine, claimed, "temporary")["state"], "queued")
        claimed = _claim(self.engine, "worker")
        self.assertEqual(claimed["attempts"], 2)
        self.assertEqual(_fail(self.engine, claimed, "permanent")["state"], "failed")

    def test_failed_dependency_marks_descendant_failed_instead_of_stranding_it(self):
        parent = self.engine.submit(self.goal, "parent", {}, idempotency_key="parent", max_attempts=1)
        child = self.engine.submit(self.goal, "child", {}, idempotency_key="child", dependencies=[parent["id"]])
        claimed = _claim(self.engine, "worker")
        _fail(self.engine, claimed, "permanent failure")
        self.assertEqual(self.engine.get_task(child["id"])["state"], "failed")
        self.assertIsNone(self.engine.claim("worker"))

    def test_expired_lease_is_terminal_unless_replay_safety_is_explicit(self):
        now = [100.0]
        engine = DurableTaskEngine(self.db, clock=lambda: now[0])
        goal = engine.create_goal()
        task = engine.submit(
            goal, "side-effect", {}, idempotency_key="lease-no-replay",
            max_attempts=3,
        )
        first_claim = _claim(engine, "worker-a", lease_seconds=5)
        now[0] += 6
        self.assertEqual(engine.recover_expired_leases(), 1)
        self.assertEqual(engine.get_task(task["id"])["state"], "failed")
        self.assertIsNone(engine.claim("worker-b"))

    def test_expired_lease_recovery_and_stale_worker_rejected(self):
        now = [100.0]
        engine = DurableTaskEngine(self.db, clock=lambda: now[0])
        goal = engine.create_goal()
        task = engine.submit(goal, "work", {}, idempotency_key="lease", max_attempts=2, retry_safe=True)
        first_claim = _claim(engine, "worker-a", lease_seconds=5)
        now[0] += 6
        self.assertEqual(engine.recover_expired_leases(), 1)
        with self.assertRaises(TaskConflict):
            engine.complete(task["id"], "worker-a", {}, lease_generation=first_claim["lease_generation"])
        second_claim = _claim(engine, "worker-a")
        with self.assertRaises(TaskConflict):
            engine.complete(task["id"], "worker-a", {}, lease_generation=first_claim["lease_generation"])
        self.assertEqual(second_claim["id"], task["id"])
        self.assertNotEqual(first_claim["lease_generation"], second_claim["lease_generation"])
        self.assertEqual(engine.complete(task["id"], "worker-a", {}, lease_generation=second_claim["lease_generation"])["state"], "succeeded")

    def test_lease_generation_is_only_returned_to_the_claiming_worker(self):
        task = self.engine.submit(
            self.goal, "work", {}, idempotency_key="private-lease-token"
        )
        claimed = _claim(self.engine, "private-token-worker")
        self.assertEqual(claimed["id"], task["id"])
        self.assertTrue(claimed["lease_generation"])
        self.assertNotIn("lease_generation", self.engine.get_task(task["id"]))
        self.assertNotIn("lease_generation", self.engine.submit(
            self.goal, "other", {}, idempotency_key="queued-no-token"
        ))

    def test_concurrent_workers_claim_distinct_tasks(self):
        first = self.engine.submit(self.goal, "work", {"n": 1}, idempotency_key="concurrent-1")
        second = self.engine.submit(self.goal, "work", {"n": 2}, idempotency_key="concurrent-2")
        barrier = threading.Barrier(2)
        claimed = []
        errors = []

        def claim(worker):
            try:
                barrier.wait(timeout=5)
                claimed.append(self.engine.claim(worker))
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
        self.assertEqual({item["id"] for item in claimed}, {first["id"], second["id"]})
        self.assertTrue(all(item["lease_generation"] for item in claimed))

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
            goal_columns = {row[1] for row in conn.execute("PRAGMA table_info(goals)")}
        finally:
            conn.close()
        self.assertIn("submission_fingerprint", columns)
        self.assertIn("retry_safe", columns)
        self.assertIn("lease_generation", columns)
        self.assertIn("goal_outcome", goal_columns)
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

        # A pre-generation live lease cannot be adopted by a post-upgrade
        # worker, even when its worker label and expiry still look valid.
        conn = sqlite3.connect(legacy_db)
        try:
            conn.execute(
                """INSERT INTO tasks(
                    id, goal_id, task_type, payload_json, state, idempotency_key,
                    attempts, max_attempts, available_at, lease_owner, lease_until,
                    created_at, updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                ("legacy-running", goal, "work", "{}", "running",
                 "legacy-running-key", 1, 3, 0, "legacy-worker",
                 9_999_999_999, 0, 0),
            )
            conn.commit()
        finally:
            conn.close()
        with self.assertRaises(TaskConflict):
            migrated.complete(
                "legacy-running", "legacy-worker", {},
                lease_generation="untrusted-legacy-generation",
            )

    def test_goal_outcome_is_failed_when_all_children_fail(self):
        task = self.engine.submit(
            self.goal, "work", {}, idempotency_key="all-failed-outcome"
        )
        claimed = _claim(self.engine, "failed-outcome-worker")
        _fail(self.engine, claimed, "permanent failure")
        goal = self.engine.get_goal(self.goal)
        self.assertEqual(goal["state"], "completed")
        self.assertEqual(goal["goal_outcome"], "failed")
        completed_events = [
            event for event in self.engine.list_events(self.goal)
            if event["event_type"] == "goal.completed"
        ]
        self.assertEqual(completed_events[-1]["detail"]["outcome"], "failed")

    def test_goal_outcome_is_mixed_for_success_and_failure(self):
        succeeded = self.engine.submit(
            self.goal, "work", {}, idempotency_key="mixed-success"
        )
        failed = self.engine.submit(
            self.goal, "work", {}, idempotency_key="mixed-failure"
        )
        first = _claim(self.engine, "mixed-worker")
        _complete(self.engine, first, {"ok": True})
        second = _claim(self.engine, "mixed-worker")
        self.assertEqual(second["id"], failed["id"])
        _fail(self.engine, second, "permanent failure")
        goal = self.engine.get_goal(self.goal)
        self.assertEqual(goal["state"], "completed")
        self.assertEqual(goal["goal_outcome"], "mixed")

    def test_cancelled_goal_records_cancelled_or_mixed_outcome(self):
        task = self.engine.submit(
            self.goal, "work", {}, idempotency_key="cancelled-outcome"
        )
        self.engine.set_goal_state(self.goal, "cancelled")
        goal = self.engine.get_goal(self.goal)
        self.assertEqual(goal["state"], "cancelled")
        self.assertEqual(goal["goal_outcome"], "cancelled")

    def test_goal_outcome_remains_null_until_all_tasks_are_terminal(self):
        self.engine.submit(
            self.goal, "work", {}, idempotency_key="pending-outcome"
        )
        self.assertIsNone(self.engine.get_goal(self.goal)["goal_outcome"])

    def test_goal_state_rejects_non_string_values_with_value_error(self):
        for invalid_state in (None, [], {}, 1):
            with self.subTest(state_type=type(invalid_state).__name__):
                with self.assertRaises(ValueError):
                    self.engine.set_goal_state(self.goal, invalid_state)

    def test_resume_reconciles_goal_completed_while_paused(self):
        task = self.engine.submit(
            self.goal, "work", {}, idempotency_key="resume-terminal-reconcile"
        )
        claimed = _claim(self.engine, "resume-worker")
        self.engine.set_goal_state(self.goal, "paused")
        _complete(self.engine, claimed, {"done": True})
        self.assertEqual(self.engine.get_goal(self.goal)["state"], "paused")
        resumed = self.engine.set_goal_state(self.goal, "active")
        self.assertEqual(resumed["state"], "completed")
        event_types = [event["event_type"] for event in self.engine.list_events(self.goal)]
        self.assertEqual(event_types.count("goal.completed"), 1)

    def test_completed_goal_cannot_be_cancelled_or_reopened(self):
        task = self.engine.submit(
            self.goal, "work", {}, idempotency_key="completed-goal-terminal"
        )
        claimed = _claim(self.engine, "terminal-worker")
        _complete(self.engine, claimed, {"done": True})
        self.assertEqual(self.engine.get_goal(self.goal)["state"], "completed")
        with self.assertRaises(TaskConflict):
            self.engine.set_goal_state(self.goal, "cancelled")
        with self.assertRaises(TaskConflict):
            self.engine.set_goal_state(self.goal, "active")
        self.assertEqual(self.engine.get_goal(self.goal)["state"], "completed")

    def test_goal_cancellation_emits_per_task_audit_events(self):
        task = self.engine.submit(
            self.goal, "work", {}, idempotency_key="goal-cancel-audit"
        )
        # A queued task can be cancelled atomically before any worker starts.
        self.engine.set_goal_state(self.goal, "cancelled")
        self.assertEqual(self.engine.get_task(task["id"])["state"], "cancelled")
        events = self.engine.list_events(self.goal)
        task_cancel_events = [
            event for event in events
            if event["task_id"] == task["id"] and event["event_type"] == "task.cancelled"
        ]
        goal_cancel_events = [
            event for event in events
            if event["task_id"] is None and event["event_type"] == "goal.cancelled"
        ]
        self.assertEqual(len(task_cancel_events), 1)
        self.assertEqual(goal_cancel_events[-1]["detail"]["cancelled_task_count"], 1)

    def test_goal_cancellation_race_with_completion_has_one_terminal_task_event(self):
        task = self.engine.submit(
            self.goal, "work", {}, idempotency_key="goal-cancel-race"
        )
        claimed = _claim(self.engine, "race-worker")
        barrier = threading.Barrier(2)
        errors = []

        def complete():
            try:
                barrier.wait(timeout=5)
                _complete(self.engine, claimed, {"done": True})
            except TaskConflict:
                # Expected if goal cancellation invalidates the lease first.
                pass
            except Exception as exc:
                errors.append(exc)

        def cancel_goal():
            try:
                barrier.wait(timeout=5)
                self.engine.set_goal_state(self.goal, "cancelled")
            except TaskConflict:
                # Expected if completion commits first and makes the goal terminal.
                pass
            except Exception as exc:
                errors.append(exc)

        workers = [
            threading.Thread(target=complete),
            threading.Thread(target=cancel_goal),
        ]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=10)
        self.assertFalse(any(worker.is_alive() for worker in workers), "race workers timed out")
        self.assertEqual(errors, [])
        final_task = self.engine.get_task(task["id"])
        # The engine refuses goal cancellation while a task is running, so
        # completion must win this race without a task-cancelled event.
        self.assertEqual(final_task["state"], "succeeded")
        events = [
            event for event in self.engine.list_events(self.goal)
            if event["task_id"] == task["id"]
            and event["event_type"] in {"task.succeeded", "task.cancelled"}
        ]
        self.assertEqual(len(events), 1)

    def test_running_task_can_only_be_recovered_after_lease_expiry(self):
        now = [100.0]
        engine = DurableTaskEngine(self.db, clock=lambda: now[0])
        goal = engine.create_goal()
        task = engine.submit(
            goal, "work", {}, idempotency_key="expiry-cancel",
            max_attempts=1,
        )
        _claim(engine, "expiry-worker", lease_seconds=5)
        with self.assertRaises(TaskConflict):
            engine.cancel_task(task["id"])
        with self.assertRaises(TaskConflict):
            engine.set_goal_state(goal, "cancelled")
        now[0] += 6
        self.assertEqual(engine.recover_expired_leases(), 1)
        self.assertEqual(engine.get_task(task["id"])["state"], "failed")
        # Exhausting the only attempt terminally completes the goal.
        # Terminal goals cannot be reopened or cancelled afterward.
        self.assertEqual(engine.get_goal(goal)["state"], "completed")
        with self.assertRaises(TaskConflict):
            engine.set_goal_state(goal, "cancelled")

    def test_running_task_cannot_be_cancelled_while_side_effect_may_be_active(self):
        task = self.engine.submit(
            self.goal, "work", {}, idempotency_key="running-cancel-guard"
        )
        claimed = _claim(self.engine, "active-worker")
        with self.assertRaises(TaskConflict):
            self.engine.cancel_task(task["id"])
        with self.assertRaises(TaskConflict):
            self.engine.set_goal_state(self.goal, "cancelled")
        self.assertEqual(self.engine.get_task(task["id"])["state"], "running")
        self.assertEqual(self.engine.get_goal(self.goal)["state"], "active")
        # The worker retains ownership and can report its real outcome.
        self.assertEqual(
            _complete(self.engine, claimed, {"done": True})["state"],
            "succeeded",
        )

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
