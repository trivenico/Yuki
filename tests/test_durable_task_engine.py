import sqlite3
from contextlib import closing
import hashlib
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
        self._install_claim_token_test_adapter(self.engine)

    def _install_claim_token_test_adapter(self, engine):
        # Existing lifecycle tests model a worker retaining its claim secret.
        # The adapter passes that secret explicitly to the strict engine API.
        tokens = {}
        claim = engine.claim
        heartbeat = engine.heartbeat
        complete = engine.complete
        fail = engine.fail
        reject = engine.reject
        engine._strict_complete = complete
        engine._strict_heartbeat = heartbeat
        def claim_with_token(worker_id, **kwargs):
            task = claim(worker_id, **kwargs)
            if task is not None:
                tokens[(task["id"], worker_id)] = task["lease_token"]
            return task
        def token_for(task_id, worker_id, kwargs):
            token = kwargs.pop("lease_token", tokens.get((task_id, worker_id)))
            if not token:
                raise AssertionError("test worker did not retain its claim token")
            return token
        def heartbeat_with_token(task_id, worker_id, **kwargs):
            return heartbeat(task_id, worker_id, lease_token=token_for(task_id, worker_id, kwargs), **kwargs)
        def complete_with_token(task_id, worker_id, result=None, **kwargs):
            return complete(task_id, worker_id, result, lease_token=token_for(task_id, worker_id, kwargs), **kwargs)
        def fail_with_token(task_id, worker_id, error, **kwargs):
            return fail(task_id, worker_id, error, lease_token=token_for(task_id, worker_id, kwargs), **kwargs)
        def reject_with_token(task_id, worker_id, reason, **kwargs):
            return reject(task_id, worker_id, reason, lease_token=token_for(task_id, worker_id, kwargs), **kwargs)
        engine.claim = claim_with_token
        engine.heartbeat = heartbeat_with_token
        engine.complete = complete_with_token
        engine.fail = fail_with_token
        engine.reject = reject_with_token

    def tearDown(self):
        self.engine.close()
        self.tmp.cleanup()

    def test_expired_claim_creates_leased_durable_revocation_outbox(self):
        task = self.engine.submit(
            self.goal, "work", {}, idempotency_key="recovery-outbox"
        )
        claimed = self.engine.claim("recovery-worker")
        self.assertEqual(claimed["id"], task["id"])
        with closing(sqlite3.connect(self.db)) as db:
            db.execute("UPDATE tasks SET lease_until=0 WHERE id=?", (task["id"],))
            db.commit()
        self.assertEqual(self.engine.recover_expired_leases(), 1)
        first = self.engine.claim_recovery_outbox("supervisor-a")
        self.assertEqual(len(first), 1)
        self.assertEqual(first[0]["task_id"], task["id"])
        self.assertEqual(first[0]["worker_id"], "recovery-worker")
        self.assertEqual(len(first[0]["claim_token_sha256"]), 64)
        self.assertNotIn("lease_token", first[0])
        # A second supervisor cannot take an item while its processing lease is live.
        self.assertEqual(self.engine.claim_recovery_outbox("supervisor-b"), [])
        self.assertTrue(self.engine.release_recovery_outbox(first[0]["id"], "supervisor-a", first[0]["claim_token"]))
        second = self.engine.claim_recovery_outbox("supervisor-b")
        self.assertEqual(len(second), 1)
        self.assertTrue(
            self.engine.acknowledge_recovery_outbox(second[0]["id"], "supervisor-b", second[0]["claim_token"])
        )
        self.assertEqual(self.engine.claim_recovery_outbox("supervisor-c"), [])

    def test_recovery_outbox_rejects_stale_supervisor_claim_token(self):
        task = self.engine.submit(
            self.goal, "work", {}, idempotency_key="recovery-outbox-fence"
        )
        claimed = self.engine.claim("recovery-worker-fence")
        self.assertEqual(claimed["id"], task["id"])
        with closing(sqlite3.connect(self.db)) as db:
            db.execute("UPDATE tasks SET lease_until=0 WHERE id=?", (task["id"],))
            db.commit()
        self.engine.recover_expired_leases()
        first = self.engine.claim_recovery_outbox("same-supervisor")
        self.assertEqual(len(first), 1)
        with sqlite3.connect(self.db) as db:
            db.execute(
                "UPDATE lease_recovery_outbox SET claim_until=0 WHERE id=?",
                (first[0]["id"],),
            )
            db.commit()
        second = self.engine.claim_recovery_outbox("same-supervisor")
        self.assertEqual(len(second), 1)
        self.assertNotEqual(first[0]["claim_token"], second[0]["claim_token"])
        self.assertFalse(self.engine.acknowledge_recovery_outbox(
            first[0]["id"], "same-supervisor", first[0]["claim_token"]
        ))
        self.assertTrue(self.engine.acknowledge_recovery_outbox(
            second[0]["id"], "same-supervisor", second[0]["claim_token"]
        ))

    def test_approval_wait_is_durable_and_stale_after_claim_recovery(self):
        task = self.engine.submit(
            self.goal, "skill.invoke", {"skill_name": "write", "argument": "x"},
            idempotency_key="durable-approval-wait", max_attempts=2,
        )
        claimed = self.engine.claim("approval-worker")
        request_id = "a" * 32
        wait = self.engine.record_approval_wait(
            task["id"], "approval-worker", claimed["lease_token"], request_id,
        )
        self.assertEqual(wait["state"], "pending")
        reopened = DurableTaskEngine(self.db)
        self.addCleanup(reopened.close)
        digest = hashlib.sha256(claimed["lease_token"].encode("ascii")).hexdigest()
        self.assertEqual(
            reopened.get_approval_wait(task["id"], digest)["request_id"], request_id
        )
        self.assertTrue(reopened.transition_approval_wait(task["id"], digest, "delivered"))
        self.assertTrue(reopened.transition_approval_wait(task["id"], digest, "delivered"))
        with closing(sqlite3.connect(self.db)) as db:
            db.execute("UPDATE tasks SET lease_until=0 WHERE id=?", (task["id"],))
            db.commit()
        self.assertEqual(reopened.recover_expired_leases(), 1)
        self.assertEqual(reopened.get_approval_wait(task["id"], digest)["state"], "stale")
        self.assertFalse(reopened.transition_approval_wait(task["id"], digest, "completed"))
        self.assertEqual(reopened.get_task(task["id"])["state"], "queued")
        replacement = reopened.claim("replacement-worker")
        self.assertNotEqual(
            __import__("hashlib").sha256(replacement["lease_token"].encode("ascii")).hexdigest(),
            digest,
        )
        self.assertIsNone(reopened.get_approval_wait(
            task["id"],
            __import__("hashlib").sha256(replacement["lease_token"].encode("ascii")).hexdigest(),
        ))

    def test_engine_close_is_idempotent_and_blocks_future_operations(self):
        engine = DurableTaskEngine(":memory:")
        self._install_claim_token_test_adapter(engine)
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
        self._install_claim_token_test_adapter(engine)
        goal = engine.create_goal()
        task = engine.submit(
            goal, "work", {"ok": True}, idempotency_key="memory-task"
        )
        claimed = engine.claim("memory-worker")
        self.assertEqual(claimed["id"], task["id"])
        completed = engine.complete(task["id"], "memory-worker", {"done": True})
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
        self.engine.claim("terminal-replay-worker")
        self.engine.complete(task["id"], "terminal-replay-worker", {"done": True})
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
            self.engine.heartbeat("missing", "worker", lease_token="invalid-token", lease_seconds=True)

        task = self.engine.submit(
            self.goal, "work", {}, idempotency_key="invalid-retry-delay"
        )
        self.engine.claim("worker")
        for invalid_retry_delay in (float("inf"), float("nan"), 10 ** 1000, True):
            with self.subTest(retry_delay_type=type(invalid_retry_delay).__name__):
                with self.assertRaises(ValueError):
                    self.engine.fail(
                        task["id"], "worker", "error",
                        retry_delay_seconds=invalid_retry_delay,
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
        self._install_claim_token_test_adapter(engine)
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
        self.assertIn("lease_token", columns)
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

    def test_goal_state_rejects_non_string_values_with_value_error(self):
        for invalid_state in (None, [], {}, 1):
            with self.subTest(state_type=type(invalid_state).__name__):
                with self.assertRaises(ValueError):
                    self.engine.set_goal_state(self.goal, invalid_state)

    def test_resume_reconciles_goal_completed_while_paused(self):
        task = self.engine.submit(
            self.goal, "work", {}, idempotency_key="resume-terminal-reconcile"
        )
        self.engine.claim("resume-worker")
        self.engine.set_goal_state(self.goal, "paused")
        self.engine.complete(task["id"], "resume-worker", {"done": True})
        self.assertEqual(self.engine.get_goal(self.goal)["state"], "paused")
        resumed = self.engine.set_goal_state(self.goal, "active")
        self.assertEqual(resumed["state"], "completed")
        event_types = [event["event_type"] for event in self.engine.list_events(self.goal)]
        self.assertEqual(event_types.count("goal.completed"), 1)

    def test_completed_goal_cannot_be_cancelled_or_reopened(self):
        task = self.engine.submit(
            self.goal, "work", {}, idempotency_key="completed-goal-terminal"
        )
        self.engine.claim("terminal-worker")
        self.engine.complete(task["id"], "terminal-worker", {"done": True})
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
        self.engine.claim("race-worker")
        barrier = threading.Barrier(2)
        errors = []

        def complete():
            try:
                barrier.wait(timeout=5)
                self.engine.complete(task["id"], "race-worker", {"done": True})
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
        engine.claim("expiry-worker", lease_seconds=5)
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
        self.engine.claim("active-worker")
        with self.assertRaises(TaskConflict):
            self.engine.cancel_task(task["id"])
        with self.assertRaises(TaskConflict):
            self.engine.set_goal_state(self.goal, "cancelled")
        self.assertEqual(self.engine.get_task(task["id"])["state"], "running")
        self.assertEqual(self.engine.get_goal(self.goal)["state"], "active")
        # The worker retains ownership and can report its real outcome.
        self.assertEqual(
            self.engine.complete(task["id"], "active-worker", {"done": True})["state"],
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


    def test_recovered_claim_token_cannot_mutate_reassigned_task(self):
        now = [100.0]
        engine = DurableTaskEngine(self.db, clock=lambda: now[0])
        self._install_claim_token_test_adapter(engine)
        goal = engine.create_goal()
        task = engine.submit(goal, "work", {}, idempotency_key="claim-fence", max_attempts=2)
        first = engine.claim("same-worker", lease_seconds=5)
        self.assertIsNotNone(first["lease_token"])
        self.assertNotIn("lease_token", engine.get_task(task["id"]))
        now[0] += 6
        self.assertEqual(engine.recover_expired_leases(), 1)
        second = engine.claim("same-worker", lease_seconds=5)
        self.assertNotEqual(first["lease_token"], second["lease_token"])
        for operation in (
            lambda: engine.heartbeat(task["id"], "same-worker", lease_token=first["lease_token"]),
            lambda: engine.complete(task["id"], "same-worker", {}, lease_token=first["lease_token"]),
            lambda: engine.fail(task["id"], "same-worker", "stale", lease_token=first["lease_token"]),
            lambda: engine.reject(task["id"], "same-worker", "invalid_payload", lease_token=first["lease_token"]),
        ):
            with self.assertRaises(TaskConflict):
                operation()
        self.assertEqual(engine.get_task(task["id"])["state"], "running")
        self.assertEqual(
            engine.complete(task["id"], "same-worker", {"ok": True}, lease_token=second["lease_token"])["state"],
            "succeeded",
        )

    def test_worker_lifecycle_methods_require_a_claim_token(self):
        task = self.engine.submit(self.goal, "work", {}, idempotency_key="claim-token-required")
        claimed = self.engine.claim("token-required-worker")
        with self.assertRaises(TypeError):
            self.engine._strict_complete(task["id"], "token-required-worker", {})
        with self.assertRaises(TypeError):
            self.engine._strict_heartbeat(task["id"], "token-required-worker")
        self.assertEqual(
            self.engine._strict_complete(task["id"], "token-required-worker", {}, lease_token=claimed["lease_token"])["state"],
            "succeeded",
        )


    def test_owner_bound_goals_isolate_goal_task_and_event_access(self):
        alice_goal = self.engine.create_owned_goal("owner-alice")
        bob_goal = self.engine.create_owned_goal("owner-bob")
        alice_task = self.engine.submit_for_owner(
            "owner-alice", alice_goal, "work", {"private": "alice"},
            idempotency_key="alice-task",
        )
        bob_task = self.engine.submit_for_owner(
            "owner-bob", bob_goal, "work", {"private": "bob"},
            idempotency_key="bob-task",
        )
        self.assertEqual(self.engine.get_goal_for_owner("owner-alice", alice_goal)["owner_id"], "owner-alice")
        self.assertEqual(self.engine.get_task_for_owner("owner-alice", alice_task["id"])["payload"]["private"], "alice")
        for operation in (
            lambda: self.engine.get_goal_for_owner("owner-bob", alice_goal),
            lambda: self.engine.get_task_for_owner("owner-bob", alice_task["id"]),
            lambda: self.engine.list_tasks_for_owner("owner-bob", alice_goal),
            lambda: self.engine.list_events_for_owner("owner-bob", alice_goal),
            lambda: self.engine.set_goal_state_for_owner("owner-bob", alice_goal, "paused"),
            lambda: self.engine.cancel_task_for_owner("owner-bob", alice_task["id"]),
            lambda: self.engine.submit_for_owner("owner-bob", alice_goal, "work", {}, idempotency_key="cross-owner"),
        ):
            with self.assertRaises(TaskNotFound):
                operation()
        self.assertEqual(self.engine.get_task(bob_task["id"])["payload"]["private"], "bob")
        self.assertEqual(self.engine.get_goal(alice_goal)["state"], "active")

    def test_legacy_unowned_goals_are_not_visible_through_owner_scoped_api(self):
        legacy_goal = self.engine.create_goal()
        self.assertIsNone(self.engine.get_goal(legacy_goal)["owner_id"])
        with self.assertRaises(TaskNotFound):
            self.engine.get_goal_for_owner("owner-alice", legacy_goal)
        with self.assertRaises(TaskNotFound):
            self.engine.submit_for_owner(
                "owner-alice", legacy_goal, "work", {}, idempotency_key="legacy"
            )

    def test_owner_identity_is_validated_and_persisted_across_restart(self):
        for invalid in ("", " owner ", "x" * 257, None, 1):
            with self.subTest(owner=invalid):
                with self.assertRaises(ValueError):
                    self.engine.create_owned_goal(invalid)
        goal = self.engine.create_owned_goal("owner-persistent")
        restarted = DurableTaskEngine(self.db)
        self.assertEqual(
            restarted.get_goal_for_owner("owner-persistent", goal)["owner_id"],
            "owner-persistent",
        )
        with self.assertRaises(TaskNotFound):
            restarted.get_goal_for_owner("owner-other", goal)

    def test_existing_database_gets_owner_identity_migration_without_claiming_legacy_rows(self):
        legacy_db = Path(self.tmp.name) / "legacy-owner.sqlite"
        conn = sqlite3.connect(legacy_db)
        try:
            conn.executescript("""
                CREATE TABLE goals(
                    id TEXT PRIMARY KEY, state TEXT NOT NULL,
                    created_at REAL NOT NULL, updated_at REAL NOT NULL
                );
                INSERT INTO goals VALUES('old-goal','active',1,1);
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
        self.assertIsNone(migrated.get_goal("old-goal")["owner_id"])
        with self.assertRaises(TaskNotFound):
            migrated.get_goal_for_owner("owner-new", "old-goal")
        new_goal = migrated.create_owned_goal("owner-new")
        self.assertEqual(migrated.get_goal_for_owner("owner-new", new_goal)["owner_id"], "owner-new")


    def test_owner_scoped_mutations_check_identity_inside_mutation_transaction(self):
        alice_goal = self.engine.create_owned_goal("owner-alice-atomic")
        bob_goal = self.engine.create_owned_goal("owner-bob-atomic")
        alice_task = self.engine.submit_for_owner(
            "owner-alice-atomic", alice_goal, "work", {},
            idempotency_key="alice-atomic-task",
        )

        # Exercise the owner-aware mutation entry points directly. They must
        # carry the identity predicate into the same BEGIN IMMEDIATE
        # transaction as the state change, rather than relying on a prior read.
        with self.assertRaises(TaskNotFound):
            self.engine.set_goal_state(
                alice_goal, "paused", owner_id="owner-bob-atomic"
            )
        with self.assertRaises(TaskNotFound):
            self.engine.cancel_task(
                alice_task["id"], owner_id="owner-bob-atomic"
            )
        self.assertEqual(self.engine.get_goal(alice_goal)["state"], "active")
        self.assertEqual(self.engine.get_task(alice_task["id"])["state"], "queued")
        self.assertEqual(self.engine.get_goal(bob_goal)["state"], "active")

    def test_owner_scoped_mutation_rejects_unowned_legacy_records(self):
        legacy_goal = self.engine.create_goal()
        task = self.engine.submit(
            legacy_goal, "work", {}, idempotency_key="legacy-owner-mutation"
        )
        with self.assertRaises(TaskNotFound):
            self.engine.set_goal_state(
                legacy_goal, "paused", owner_id="owner-new"
            )
        with self.assertRaises(TaskNotFound):
            self.engine.cancel_task(task["id"], owner_id="owner-new")
        self.assertEqual(self.engine.get_goal(legacy_goal)["state"], "active")
        self.assertEqual(self.engine.get_task(task["id"])["state"], "queued")

    def test_claim_identity_is_derived_from_goal_and_current_lease(self):
        owner_id = "trusted-owner"
        goal_id = self.engine.create_owned_goal(owner_id)
        task = self.engine.submit_for_owner(
            owner_id, goal_id, "work", {}, idempotency_key="claim-identity",
        )
        first = self.engine.claim("identity-worker", lease_seconds=30)
        self.assertEqual(first["id"], task["id"])
        identity = self.engine.get_claim_identity(
            task["id"], "identity-worker", first["lease_token"]
        )
        self.assertEqual(identity, {
            "owner_id": owner_id, "goal_id": goal_id, "task_id": task["id"]
        })
        self.assertFalse(self.engine.validate_lease(
            task["id"], "identity-worker", "invalid-claim-token"
        ))

    def test_claim_identity_marks_legacy_goal_unowned(self):
        goal_id = self.engine.create_goal()
        task = self.engine.submit(
            goal_id, "work", {}, idempotency_key="legacy-claim-identity",
        )
        claim = self.engine.claim("legacy-worker", lease_seconds=30)
        self.assertEqual(claim["id"], task["id"])
        identity = self.engine.get_claim_identity(
            task["id"], "legacy-worker", claim["lease_token"]
        )
        self.assertIsNone(identity["owner_id"])


if __name__ == "__main__":
    unittest.main()
