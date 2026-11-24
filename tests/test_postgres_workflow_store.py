"""Real PostgreSQL integration tests for the shared workflow repository.

Set YUKI_TEST_POSTGRES_URL to a disposable PostgreSQL database. These tests
skip when PostgreSQL is unavailable; SQLite is covered by the existing suite.
"""
import os
from pathlib import Path
import threading
import unittest
import uuid

POSTGRES_URL = os.environ.get("YUKI_TEST_POSTGRES_URL")
try:
    import psycopg
except ImportError:  # pragma: no cover
    psycopg = None

_ENABLED = bool(POSTGRES_URL and psycopg)


def _claim_in_process(database_url, worker_id, barrier, result_queue):
    """Independent OS-process claimant used by the PostgreSQL integration suite."""
    from automation.postgres_workflow_store import PostgresWorkflowRunStore
    store = None
    try:
        store = PostgresWorkflowRunStore(database_url, max_pool_size=1)
        barrier.wait(timeout=15)
        claimed = store.claim_next(worker_id=worker_id)
        result_queue.put((worker_id, None if claimed is None else {
            "run_id": claimed.run_id,
            "lease_generation": claimed.lease_generation,
            "lease_owner": claimed.lease_owner,
        }, None))
    except Exception as exc:
        result_queue.put((worker_id, None, type(exc).__name__ + ":" + str(exc)))
    finally:
        if store is not None:
            store.close()


@unittest.skipUnless(_ENABLED, "YUKI_TEST_POSTGRES_URL and psycopg are required")
class PostgresWorkflowRepositoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from automation.postgres_workflow_store import PostgresWorkflowRunStore
        cls.store_type = PostgresWorkflowRunStore
        migration = Path(__file__).resolve().parents[1] / "migrations/security/0007_workflow_runs.sql"
        with psycopg.connect(POSTGRES_URL) as conn:
            conn.execute(migration.read_text(encoding="utf-8"), prepare=False)

    def setUp(self):
        self.key = "pg-workflow-" + uuid.uuid4().hex
        self.store_a = self.store_type(POSTGRES_URL, max_pool_size=2)
        self.store_b = self.store_type(POSTGRES_URL, max_pool_size=2)
        self.run = self.store_a.create(
            idempotency_key=self.key,
            workflow_name="postgres-contract",
            definition=[{"name": "work", "action": "emit", "argument": "safe",
                         "requires_approval": False}],
        )

    def tearDown(self):
        with psycopg.connect(POSTGRES_URL) as conn:
            conn.execute("DELETE FROM workflow_runs WHERE idempotency_key LIKE %s", (self.key + "%",))
        self.store_a.close()
        self.store_b.close()

    def test_idempotency_and_payload_conflict(self):
        same = self.store_b.create(
            idempotency_key=self.key, workflow_name="postgres-contract",
            definition=self.run.definition,
        )
        self.assertEqual(same.run_id, self.run.run_id)
        from automation.workflow_store import WorkflowConflict
        with self.assertRaises(WorkflowConflict):
            self.store_b.create(
                idempotency_key=self.key, workflow_name="different",
                definition=self.run.definition,
            )

    def test_independent_connections_only_claim_once(self):
        barrier = threading.Barrier(2)
        claims, errors = [], []

        def claim(store, worker):
            try:
                barrier.wait(timeout=5)
                claims.append(store.claim_next(worker_id=worker))
            except Exception as exc:
                errors.append(exc)

        threads = [
            threading.Thread(target=claim, args=(self.store_a, "worker_a")),
            threading.Thread(target=claim, args=(self.store_b, "worker_b")),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
        self.assertEqual(errors, [])
        winners = [item for item in claims if item is not None]
        self.assertEqual(len(winners), 1)
        self.assertEqual(winners[0].run_id, self.run.run_id)
        self.assertTrue(winners[0].lease_generation)

    def test_approval_and_event_commit_together_before_claim(self):
        self.store_a.claim_next(worker_id="worker_a")
        protected = self.store_a.create(
            idempotency_key=self.key + "-approval", workflow_name="protected",
            definition=[{"name": "work", "action": "emit", "argument": "safe",
                         "requires_approval": True}],
        )
        self.assertIsNone(self.store_a.claim_next(worker_id="worker_a"))
        approved = self.store_a.approve_with_event(
            protected.run_id, actor_id="reviewer", session_id="session",
            expected_version=protected.version,
        )
        self.assertEqual(approved.version, 2)
        events = self.store_a.list_task_events(protected.run_id)
        self.assertEqual([event.kind for event in events], ["task.approved"])
        claim = self.store_b.claim_next(worker_id="worker_b")
        self.assertEqual(claim.run_id, protected.run_id)

    def test_worker_mutation_is_fenced_by_generation_and_database_expiry(self):
        claimed = self.store_a.claim_next(worker_id="worker_a")
        self.assertEqual(claimed.run_id, self.run.run_id)
        with psycopg.connect(POSTGRES_URL) as conn:
            conn.execute(
                "UPDATE workflow_runs SET lease_expires_at=clock_timestamp()-interval '1 second' "
                "WHERE run_id=%s", (claimed.run_id,),
            )
        from automation.workflow_store import WorkflowConflict
        with self.assertRaises(WorkflowConflict):
            self.store_b.append_task_event(
                claimed.run_id, kind="task.progress", actor_id="worker",
                payload={"message": "late"}, expected_version=claimed.version,
                expected_lease_owner="worker_a",
                expected_lease_generation=claimed.lease_generation,
            )

    def test_lease_renewal_preserves_claim_generation_and_fence(self):
        claimed = self.store_a.claim_next(worker_id="worker_a")
        renewed = self.store_b.renew_lease(
            claimed.run_id, worker_id="worker_a", expected_version=claimed.version,
            lease_seconds=90,
        )
        self.assertEqual(renewed.version, claimed.version + 1)
        self.assertEqual(renewed.lease_generation, claimed.lease_generation)
        event = self.store_a.append_task_event(
            claimed.run_id, kind="task.progress", actor_id="worker",
            payload={"message": "same claim remains authorized after renewal"},
            expected_version=renewed.version, expected_lease_owner="worker_a",
            expected_lease_generation=claimed.lease_generation,
        )
        self.assertEqual(event.kind, "task.progress")

    def test_transition_and_terminal_event_share_one_versioned_transaction(self):
        claimed = self.store_a.claim_next(worker_id="worker_a")
        completed = self.store_a.transition_with_event(
            claimed.run_id, expected_version=claimed.version, to_state="succeeded",
            event_kind="task.completed", actor_id="service",
            event_payload={"state_version": claimed.version + 1},
            result={"ok": True}, expected_lease_owner="worker_a",
            expected_lease_generation=claimed.lease_generation,
        )
        self.assertEqual(completed.state, "succeeded")
        self.assertEqual(completed.version, claimed.version + 2)
        self.assertEqual([event.kind for event in self.store_a.list_task_events(claimed.run_id)],
                         ["task.completed"])
        self.assertIsNone(completed.lease_owner)


    def test_independent_processes_claim_distinct_runs_without_duplicate_ownership(self):
        import multiprocessing
        second = self.store_a.create(
            idempotency_key=self.key + "-second",
            workflow_name="postgres-contract",
            definition=self.run.definition,
        )
        context = multiprocessing.get_context("spawn")
        barrier = context.Barrier(2)
        results = context.Queue()
        processes = [
            context.Process(
                target=_claim_in_process,
                args=(POSTGRES_URL, "process_worker_a", barrier, results),
            ),
            context.Process(
                target=_claim_in_process,
                args=(POSTGRES_URL, "process_worker_b", barrier, results),
            ),
        ]
        for process in processes:
            process.start()
        for process in processes:
            process.join(timeout=30)
        for process in processes:
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)
        self.assertEqual([process.exitcode for process in processes], [0, 0])
        received = [results.get(timeout=5) for _ in processes]
        errors = [item for item in received if item[2] is not None]
        self.assertEqual(errors, [])
        claims = [item[1] for item in received if item[1] is not None]
        self.assertEqual(len(claims), 2)
        self.assertEqual({item["run_id"] for item in claims}, {self.run.run_id, second.run_id})
        self.assertEqual({item["lease_owner"] for item in claims}, {"process_worker_a", "process_worker_b"})
        self.assertTrue(all(item["lease_generation"] for item in claims))

    def test_expired_lease_recovery_is_atomic_and_never_auto_replays(self):
        from automation.workflow_store import WorkflowConflict
        claimed = self.store_a.claim_next(worker_id="crashed_worker")
        self.assertEqual(claimed.run_id, self.run.run_id)
        with psycopg.connect(POSTGRES_URL) as conn:
            conn.execute(
                "UPDATE workflow_runs SET lease_expires_at=clock_timestamp()-interval '1 second' "
                "WHERE run_id=%s", (claimed.run_id,),
            )
        recovered = self.store_b.recover_expired_with_events(actor_id="recovery_service", limit=10)
        self.assertEqual([item.run_id for item in recovered], [claimed.run_id])
        self.assertEqual(recovered[0].state, "interrupted")
        self.assertIsNone(recovered[0].lease_owner)
        self.assertIsNone(recovered[0].lease_expires_at)
        events = self.store_a.list_task_events(claimed.run_id)
        self.assertEqual([event.kind for event in events], ["task.interrupted"])
        self.assertFalse(events[0].payload["automatic_replay"])
        with self.assertRaises(WorkflowConflict):
            self.store_a.append_task_event(
                claimed.run_id, kind="task.progress", actor_id="crashed_worker",
                payload={"message": "stale process resumed"},
                expected_version=claimed.version, expected_lease_owner="crashed_worker",
                expected_lease_generation=claimed.lease_generation,
            )
        self.assertIsNone(self.store_a.claim_next(worker_id="replacement_worker"))


if __name__ == "__main__":
    unittest.main()
