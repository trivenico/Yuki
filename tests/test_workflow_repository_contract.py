import tempfile
import unittest
from pathlib import Path

from automation.task_runtime import TaskRuntime
from automation.workflow_repository import ManagedWorkflowRepository, WorkflowRepository
from automation.workflow_store import WorkflowRunStore


class DelegatingRepository:
    """Structural adapter used to prove TaskRuntime is not SQLite-class-bound."""

    def __init__(self, delegate):
        self.delegate = delegate

    def __getattr__(self, name):
        return getattr(self.delegate, name)


class WorkflowRepositoryContractTests(unittest.TestCase):
    def test_sqlite_store_satisfies_backend_neutral_contract(self):
        with tempfile.TemporaryDirectory() as directory:
            store = WorkflowRunStore(str(Path(directory) / "tasks.sqlite3"))
            self.assertIsInstance(store, WorkflowRepository)
            self.assertNotIsInstance(store, ManagedWorkflowRepository)
            runtime = TaskRuntime(store, authorize=lambda actor, action, task: True)
            self.assertIs(runtime.store, store)

    def test_structural_adapter_is_accepted_without_sqlite_inheritance(self):
        with tempfile.TemporaryDirectory() as directory:
            sqlite_store = WorkflowRunStore(str(Path(directory) / "tasks.sqlite3"))
            adapter = DelegatingRepository(sqlite_store)
            runtime = TaskRuntime(adapter, authorize=lambda actor, action, task: True)
            self.assertIs(runtime.store, adapter)
            task = runtime.enqueue(
                actor_id="operator",
                idempotency_key="adapter-contract",
                name="contract-test",
                steps=[{"name": "step", "action": "emit", "argument": "safe",
                        "requires_approval": False}],
            )
            self.assertEqual(task.state, "queued")
            self.assertEqual([event.kind for event in runtime.events(
                actor_id="operator", task_id=task.task_id
            )], ["task.created"])

    def test_incomplete_repository_is_rejected_with_missing_capabilities(self):
        class Incomplete:
            def get(self, run_id):
                return None

        with self.assertRaisesRegex(TypeError, "WorkflowRepository.*create"):
            TaskRuntime(Incomplete())
