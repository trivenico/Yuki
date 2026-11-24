"""PostgreSQL integration tests for external-operation idempotency and recovery."""
import os
from pathlib import Path
import unittest
import uuid

POSTGRES_URL = os.environ.get("YUKI_TEST_POSTGRES_URL")
try:
    import psycopg
except ImportError:  # pragma: no cover
    psycopg = None

_ENABLED = bool(POSTGRES_URL and psycopg)


@unittest.skipUnless(_ENABLED, "YUKI_TEST_POSTGRES_URL and psycopg are required")
class PostgresExternalOperationStoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from automation.postgres_external_operation_store import PostgresExternalOperationStore
        cls.store_type = PostgresExternalOperationStore
        root = Path(__file__).resolve().parents[1]
        with psycopg.connect(POSTGRES_URL) as conn:
            conn.execute((root / "migrations/security/0007_workflow_runs.sql").read_text(), prepare=False)
            conn.execute((root / "migrations/security/0009_external_operations.sql").read_text(), prepare=False)
            applied = conn.execute("SELECT 1 FROM pg_constraint WHERE conname=%s", ("workflow_runs_ownership_status_check",)).fetchone()
            if not applied:
                conn.execute((root / "migrations/security/0010_workflow_task_ownership.sql").read_text(encoding="utf-8"), prepare=False)

    def setUp(self):
        from automation.postgres_workflow_store import PostgresWorkflowRunStore
        self.workflow_store = PostgresWorkflowRunStore(POSTGRES_URL, max_pool_size=2)
        self.store = self.store_type(POSTGRES_URL, max_pool_size=2, authorize_reconciliation=lambda actor, operation, version: actor == "operator")
        self.key = "operation-" + uuid.uuid4().hex
        self.task = self.workflow_store.create(
            idempotency_key=self.key, workflow_name="external-operation-test",
            definition=[{"name": "intent", "action": "emit", "argument": "metadata",
                         "requires_approval": False}],
            tenant_id="tenant-test", owner_principal_id="human:test-owner",
        )

    def tearDown(self):
        with psycopg.connect(POSTGRES_URL) as conn:
            conn.execute("DELETE FROM external_operation_events WHERE operation_id IN (SELECT operation_id FROM external_operations WHERE task_id=%s)", (self.task.run_id,))
            conn.execute("DELETE FROM external_operations WHERE task_id=%s", (self.task.run_id,))
            conn.execute("DELETE FROM workflow_runs WHERE run_id=%s", (self.task.run_id,))
        self.store.close()
        self.workflow_store.close()

    def create_operation(self, key=None, input_digest=None):
        return self.store._create_unchecked(
            tenant_id="tenant-test", task_id=self.task.run_id, step_id="step-1",
            action_name="calendar.create", action_version="v1",
            idempotency_key=key or self.key,
            input_digest=input_digest or "a" * 64,
        )

    def test_operation_tenant_must_match_owned_parent_task(self):
        with self.assertRaises(psycopg.IntegrityError):
            self.store._create_unchecked(
                tenant_id="tenant-other", task_id=self.task.run_id, step_id="step-1",
                action_name="calendar.create", action_version="v1",
                idempotency_key="wrong-tenant-" + uuid.uuid4().hex,
                input_digest="a" * 64,
            )

    def test_duplicate_intent_is_idempotent_and_conflict_is_rejected(self):
        from automation.workflow_store import WorkflowConflict
        first = self.create_operation()
        duplicate = self.create_operation()
        self.assertEqual(first.operation_id, duplicate.operation_id)
        self.assertEqual(len(self.store._list_events_unchecked(first.operation_id)), 1)
        with self.assertRaises(WorkflowConflict):
            self.create_operation(input_digest="b" * 64)

    def test_claim_and_success_are_fenced_and_atomic_with_events(self):
        operation = self.create_operation()
        claimed = self.store._claim_next_unscoped(worker_id="worker-a")
        self.assertEqual(claimed.operation_id, operation.operation_id)
        completed = self.store._complete_unchecked(
            claimed.operation_id, worker_id="worker-a",
            lease_generation=claimed.lease_generation,
            expected_version=claimed.version, result_digest="c" * 64,
            provider_reference="provider-op-123",
        )
        self.assertEqual(completed.state, "succeeded")
        self.assertEqual([e["kind"] for e in self.store._list_events_unchecked(operation.operation_id)],
                         ["operation.created", "operation.claimed",
                          "operation.succeeded"])

    def test_expired_claim_becomes_unknown_and_is_not_replayed(self):
        operation = self.create_operation()
        claimed = self.store._claim_next_unscoped(worker_id="worker-a")
        with psycopg.connect(POSTGRES_URL) as conn:
            conn.execute(
                "UPDATE external_operations SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE operation_id=%s",
                (operation.operation_id,),
            )
        self.assertIsNone(self.store._claim_next_unscoped(worker_id="worker-b"))
        unknown = self.store._get_unchecked(operation.operation_id)
        self.assertEqual(unknown.state, "unknown")
        from automation.workflow_store import WorkflowConflict
        with self.assertRaises(WorkflowConflict):
            self.store._complete_unchecked(
                operation.operation_id, worker_id="worker-a",
                lease_generation=claimed.lease_generation,
                expected_version=claimed.version, result_digest="d" * 64,
            )
        events = self.store._list_events_unchecked(operation.operation_id)
        self.assertEqual(events[-1]["kind"], "operation.unknown")
        self.assertFalse(events[-1]["payload_json"]["automatic_replay"])

    def test_explicit_reconciliation_never_requeues(self):
        operation = self.create_operation()
        claimed = self.store._claim_next_unscoped(worker_id="worker-a")
        unknown = self.store._mark_unknown_unchecked(
            operation.operation_id, worker_id="worker-a",
            lease_generation=claimed.lease_generation,
            expected_version=claimed.version, reason_code="provider_timeout",
        )
        reconciled = self.store.record_reconciliation(
            operation.operation_id, actor_id="operator",
            expected_version=unknown.version, outcome="succeeded",
            result_digest="e" * 64, provider_reference="provider-confirmed-9",
        )
        self.assertEqual(reconciled.state, "succeeded")
        self.assertIsNone(self.store._claim_next_unscoped(worker_id="worker-b"))

    def test_reconciliation_fails_closed_without_authorized_operator(self):
        operation = self.create_operation()
        claimed = self.store._claim_next_unscoped(worker_id="worker-a")
        unknown = self.store._mark_unknown_unchecked(
            operation.operation_id, worker_id="worker-a",
            lease_generation=claimed.lease_generation,
            expected_version=claimed.version, reason_code="provider_timeout",
        )
        with self.assertRaises(PermissionError):
            self.store.record_reconciliation(
                operation.operation_id, actor_id="intruder",
                expected_version=unknown.version, outcome="succeeded",
                result_digest="f" * 64,
            )

    def test_terminal_failure_cannot_be_claimed_again(self):
        self.create_operation()
        claimed = self.store._claim_next_unscoped(worker_id="worker-a")
        failed = self.store._fail_terminal_unchecked(
            claimed.operation_id, worker_id="worker-a",
            lease_generation=claimed.lease_generation,
            expected_version=claimed.version, error_code="provider_rejected",
        )
        self.assertEqual(failed.state, "failed_terminal")
        self.assertIsNone(self.store._claim_next_unscoped(worker_id="worker-b"))


if __name__ == "__main__":
    unittest.main()
