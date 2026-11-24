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
            conn.execute("DELETE FROM workflow_runs WHERE idempotency_key=%s", (self.key,))
        self.store_a.close()
        self.store_b.close()

    def test_idempotency_and_payload_conflict(self):
        same = self.store_b.create(
            idempotency_key=self.key, workflow_name="postgres-contract",
            definition=self.run.definition,
        )
        self.assertEqual(same.run_id, self.run.run_id)
        with self.assertRaises(Exception):
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


if __name__ == "__main__":
    unittest.main()
