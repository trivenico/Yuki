import tempfile
import unittest
from pathlib import Path

from automation.agent_coordinator import SpecialistAgent, SpecialistAgentCoordinator
from automation.capability_discovery import discover_capabilities
from automation.task_runtime import TaskRuntime
from automation.workflow_store import WorkflowRunStore


class CapabilityDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        db = str(Path(self.tmp.name) / "yuki.sqlite3")
        store = WorkflowRunStore(db)
        runtime = TaskRuntime(
            store,
            authorize=lambda actor, action, task: actor == "alice",
        )
        authorize = lambda actor, action, resource: (
            actor == "alice" and action in {"agent:read", "agent:register"}
        )
        self.coordinator = SpecialistAgentCoordinator(
            runtime,
            db,
            [SpecialistAgent(
                "reviewer-01", "Code review handler",
                frozenset({"code-reviewer"}), "emit",
            )],
            authorize=authorize,
        )

    def test_discovery_matches_only_explicitly_registered_enabled_agents(self):
        results = discover_capabilities(
            coordinator=self.coordinator, actor_id="alice",
            query="review code security", limit=5,
        )
        reviewer = next(item for item in results if item["capability_id"] == "code-reviewer")
        self.assertTrue(reviewer["available"])
        self.assertEqual(reviewer["registered_agent_ids"], ("reviewer-01",))
        self.assertEqual(reviewer["status"], "planned")
        self.assertEqual(reviewer["execution_mode"], "not_implemented")

    def test_unimplemented_capability_is_proposal_not_agent(self):
        results = discover_capabilities(
            coordinator=self.coordinator, actor_id="alice",
            query="GPU CUDA acceleration", limit=10,
        )
        self.assertTrue(results)
        self.assertTrue(all(item["available"] is False for item in results))
        self.assertTrue(all(item["registered_agent_ids"] == () for item in results))
        registered = self.coordinator.list_agents(actor_id="alice")
        self.assertEqual(tuple(item["agent_id"] for item in registered), ("reviewer-01",))

    def test_discovery_requires_registry_read_authorization(self):
        with self.assertRaises(PermissionError):
            discover_capabilities(
                coordinator=self.coordinator, actor_id="mallory",
                query="code review",
            )

    def test_discovery_is_read_only_and_does_not_dispatch(self):
        before = self.coordinator.list_agents(actor_id="alice")
        discover_capabilities(
            coordinator=self.coordinator, actor_id="alice",
            query="code review",
        )
        after = self.coordinator.list_agents(actor_id="alice")
        self.assertEqual(before, after)

    def test_invalid_coordinator_rejected(self):
        with self.assertRaises(TypeError):
            discover_capabilities(
                coordinator=object(), actor_id="alice", query="research",
            )


if __name__ == "__main__":
    unittest.main()
