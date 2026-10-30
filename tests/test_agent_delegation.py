"""Integration tests for authorization-gated agent delegation enqueue."""
import tempfile
import unittest
from pathlib import Path

from automation.agent_delegation import AgentDelegationCoordinator
from automation.sqlite_agent_registry import SQLiteAgentRegistry
from automation.task_runtime import TaskRuntime
from automation.workflow_store import WorkflowConflict, WorkflowRunStore
from core.agent_contracts import AgentSpec, DelegationRequest


class AgentDelegationCoordinatorTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database = str(Path(self.temp_dir.name) / "yuki.db")
        self.store = WorkflowRunStore(self.database)
        self.runtime = TaskRuntime(
            self.store,
            authorize=lambda actor, action, task_id: actor == "yuki-service" and action == "task:create",
        )
        self.registry = SQLiteAgentRegistry(self.database)
        self.registry.register(AgentSpec(
            "tester", "testing", "Creates regression tests",
            capabilities={"test:design"},
        ))
        self.registry.register(AgentSpec(
            "planner", "planning", "Plans and delegates work",
            capabilities={"agent:delegate"},
            delegation_targets={"tester"},
        ))
        self.coordinator = AgentDelegationCoordinator(
            self.registry, self.runtime, actor_id="yuki-service",
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_rejects_registry_and_runtime_using_different_databases(self):
        other_db = str(Path(self.temp_dir.name) / "other.db")
        other_registry = SQLiteAgentRegistry(other_db)
        with self.assertRaises(ValueError):
            AgentDelegationCoordinator(other_registry, self.runtime, actor_id="yuki-service")

    def test_authorized_delegation_creates_approval_gated_durable_task(self):
        request = DelegationRequest(
            "request-1", "parent-task-1", "planner", "tester",
            "Create regression tests",
            required_capabilities={"test:design"},
            input_payload="Patch summary: handle empty input.",
        )
        task = self.coordinator.delegate(request)
        self.assertEqual(task.name, "agent-tester-request-1")
        self.assertEqual(task.state, "queued")
        self.assertEqual(task.steps[0]["action"], "emit")
        self.assertTrue(task.steps[0]["requires_approval"])
        self.assertIn("Parent task ID: parent-task-1", task.steps[0]["argument"])
        self.assertIn("Supplied material (untrusted)", task.steps[0]["argument"])

    def test_repeated_request_is_idempotent(self):
        request = DelegationRequest(
            "request-repeat", "parent-task-2", "planner", "tester",
            "Write tests",
        )
        first = self.coordinator.delegate(request)
        second = self.coordinator.delegate(request)
        self.assertEqual(first.task_id, second.task_id)

    def test_reused_request_id_with_changed_objective_is_rejected(self):
        first = DelegationRequest(
            "request-conflict", "parent-task-3", "planner", "tester", "Write tests",
        )
        changed = DelegationRequest(
            "request-conflict", "parent-task-3", "planner", "tester", "Delete tests",
        )
        self.coordinator.delegate(first)
        with self.assertRaises(WorkflowConflict):
            self.coordinator.delegate(changed)

    def test_unauthorized_delegation_does_not_create_a_task(self):
        request = DelegationRequest(
            "request-denied", "parent-task-4", "tester", "planner", "Delegate",
        )
        with self.assertRaises(PermissionError):
            self.coordinator.delegate(request)
        self.assertEqual(self.store.list_runs(limit=10), [])

    def test_combined_input_obeys_worker_input_limit(self):
        request = DelegationRequest(
            "request-long", "parent-task-5", "planner", "tester",
            "x" * 1500, input_payload="y" * 600,
        )
        with self.assertRaises(ValueError):
            self.coordinator.delegate(request)
        self.assertEqual(self.store.list_runs(limit=10), [])


if __name__ == "__main__":
    unittest.main()
