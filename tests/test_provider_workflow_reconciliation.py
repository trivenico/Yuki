"""Tests for provider/workflow reconciliation journal correlation."""
import tempfile
import unittest
from pathlib import Path

from automation.task_runtime import TaskRuntime
from automation.workflow_store import WorkflowRunStore
from providers.durable_provider_governance import SQLiteProviderGovernanceStore
from providers.provider_workflow_reconciliation import ProviderWorkflowReconciliation


class ProviderWorkflowReconciliationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.clock_value = [1000.0]
        self.provider_store = SQLiteProviderGovernanceStore(
            str(Path(self.tmp.name) / "provider.sqlite3"),
            lease_timeout_seconds=1, clock=lambda: self.clock_value[0],
        )
        self.workflow_store = WorkflowRunStore(str(Path(self.tmp.name) / "workflow.sqlite3"))
        self.grants = {"task:operation:resolve"}
        self.runtime = TaskRuntime(
            self.workflow_store,
            authorize=lambda actor, action, task: actor == "owner" and action in self.grants,
        )
        self.bridge = ProviderWorkflowReconciliation(
            task_runtime=self.runtime, provider_store=self.provider_store,
        )

    def test_execution_lookup_returns_only_correlation_metadata(self):
        lease = self.provider_store.acquire(provider_id="localengine", agent_id="assistant")
        record = self.provider_store.get_execution(lease.execution_id)
        self.assertEqual(record["execution_id"], lease.execution_id)
        self.assertEqual(record["provider_id"], "localengine")
        self.assertNotIn("request", record)
        self.assertNotIn("prompt", record)

    def test_missing_execution_is_not_treated_as_reconciled(self):
        with self.assertRaises(ValueError):
            self.provider_store.get_execution("0" * 32 + "x")

    def _uncertain_pair(self):
        run = self.workflow_store.create(
            idempotency_key="workflow-once", workflow_name="external-action",
            definition=[{"name": "send", "action": "provider.call", "requires_approval": False}],
        )
        self.workflow_store.claim_next(worker_id="worker-a")
        operation = self.workflow_store.prepare_operation(
            run.run_id, idempotency_key="external-once", action="provider.call",
            request={"payload_digest": "safe"}, principal_id="owner",
        )
        inflight = self.workflow_store.mark_operation_in_flight(
            operation.operation_id, worker_id="worker-a", provider_name="localengine",
            expected_version=operation.version, principal_id="owner",
        )
        uncertain_operation = self.workflow_store.record_operation_timeout(
            inflight.operation_id, expected_version=inflight.version,
            timeout_seconds=30, principal_id="owner",
        )
        lease = self.provider_store.acquire(provider_id="localengine", agent_id="assistant")
        self.clock_value[0] += 2
        self.provider_store.snapshot(provider_id="localengine", agent_id="assistant")
        self.assertEqual(self.provider_store.get_execution(lease.execution_id)["status"], "uncertain")
        return uncertain_operation, lease.execution_id

    def test_reconciles_both_journals_and_identical_retry_is_idempotent(self):
        operation, execution_id = self._uncertain_pair()
        args = dict(
            actor_id="owner", operation_id=operation.operation_id,
            execution_id=execution_id, expected_version=operation.version,
            provider_reference="provider-request-123",
            evidence_source="localengine", evidence_sha256="a" * 64,
            resolver_id="assistant",
        )
        result = self.bridge.reconcile_success(**args)
        self.assertEqual(result.status, "succeeded")
        self.assertEqual(result.provider_reference, "provider-request-123")
        self.assertEqual(
            self.provider_store.get_execution(execution_id)["status"], "succeeded",
        )
        retried = self.bridge.reconcile_success(**args)
        self.assertEqual(retried.version, result.version)

    def test_mismatched_provider_is_rejected_before_provider_resolution(self):
        operation, execution_id = self._uncertain_pair()
        with self.assertRaisesRegex(Exception, "evidence_source"):
            self.bridge.reconcile_success(
                actor_id="owner", operation_id=operation.operation_id,
                execution_id=execution_id, expected_version=operation.version,
                provider_reference="provider-request-123",
                evidence_source="otherprovider", evidence_sha256="b" * 64,
                resolver_id="assistant",
            )
        self.assertEqual(self.provider_store.get_execution(execution_id)["status"], "uncertain")

    def test_unauthorized_actor_cannot_reconcile(self):
        operation, execution_id = self._uncertain_pair()
        with self.assertRaises(PermissionError):
            self.bridge.reconcile_success(
                actor_id="intruder", operation_id=operation.operation_id,
                execution_id=execution_id, expected_version=operation.version,
                provider_reference="provider-request-123",
                evidence_source="localengine", evidence_sha256="c" * 64,
                resolver_id="assistant",
            )
        self.assertEqual(self.provider_store.get_execution(execution_id)["status"], "uncertain")



    def test_reconciliation_creates_explicit_one_to_one_correlation(self):
        operation, execution_id = self._uncertain_pair()
        args = dict(
            actor_id="owner", operation_id=operation.operation_id,
            execution_id=execution_id, expected_version=operation.version,
            provider_reference="provider-request-456", evidence_source="localengine",
            evidence_sha256="d" * 64, resolver_id="assistant",
        )
        self.bridge.reconcile_success(**args)
        link = self.provider_store.get_workflow_correlation(execution_id=execution_id)
        self.assertEqual(link["operation_id"], operation.operation_id)
        self.assertEqual(
            self.provider_store.get_workflow_correlation(operation_id=operation.operation_id)["execution_id"],
            execution_id,
        )
        self.assertEqual(
            self.provider_store.get_workflow_correlation(execution_id=execution_id),
            self.provider_store.get_workflow_correlation(operation_id=operation.operation_id),
        )

if __name__ == "__main__":
    unittest.main()
