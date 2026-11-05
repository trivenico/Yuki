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
        lease = self.provider_store.acquire(
            provider_id="localengine", agent_id="assistant",
        )
        record = self.provider_store.get_execution(lease.execution_id)
        self.assertEqual(record["execution_id"], lease.execution_id)
        self.assertEqual(record["provider_id"], "localengine")
        self.assertNotIn("request", record)
        self.assertNotIn("prompt", record)

    def test_missing_execution_is_not_treated_as_reconciled(self):
        with self.assertRaises(KeyError):
            self.provider_store.get_execution("0" * 32 + "x")


if __name__ == "__main__":
    unittest.main()
