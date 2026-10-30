"""Tests for the persisted agent-fleet worker composition root."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from automation.agent_runtime import build_registered_agent_worker
from automation.default_agents import seed_starter_agents
from automation.sqlite_agent_registry import SQLiteAgentRegistry
from automation.task_runtime import TaskRuntime
from automation.workflow_store import WorkflowRunStore


class AgentRuntimeCompositionTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database = str(Path(self.temp_dir.name) / "yuki.db")
        self.registry = SQLiteAgentRegistry(self.database)
        seed_starter_agents(self.registry)
        self.runtime = TaskRuntime(
            WorkflowRunStore(self.database),
            authorize=lambda actor, action, task_id: actor == "worker" and action == "task:create",
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_builds_worker_from_registered_agent_specs(self):
        provider = Mock()
        provider.is_available.return_value = True
        provider.generate.return_value = "bounded output"
        manager = Mock()
        manager.current.return_value.name = "ollama"
        manager.create_routed_adapter.return_value = provider

        worker = build_registered_agent_worker(
            self.runtime, self.registry, manager,
            worker_id="agent-worker", service_actor_id="worker",
        )

        self.assertEqual(set(worker.handlers), {spec.agent_id for spec in self.registry.list_agents()})
        self.assertEqual(len(worker.handlers), 20)
        self.assertEqual(len(worker.workflow_prefix_limits), 20)
        self.assertEqual(worker.workflow_prefix_limits["agent-research-analyst-"], 2)

    def test_rejects_split_registry_and_task_databases(self):
        other = SQLiteAgentRegistry(str(Path(self.temp_dir.name) / "other.db"))
        with self.assertRaises(ValueError):
            build_registered_agent_worker(
                self.runtime, other, Mock(),
                worker_id="agent-worker", service_actor_id="worker",
            )


if __name__ == "__main__":
    unittest.main()
