"""Tests for the durable SQLite specialist-agent registry."""
import tempfile
import unittest
from pathlib import Path

from automation.sqlite_agent_registry import SQLiteAgentRegistry
from core.agent_contracts import AgentSpec, DelegationRequest


class SQLiteAgentRegistryTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database = str(Path(self.temp_dir.name) / "yuki.db")
        self.registry = SQLiteAgentRegistry(self.database, max_agents=3)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_persists_agent_specs_across_instances(self):
        spec = AgentSpec(
            "tester", "testing", "Creates test plans",
            capabilities={"test:design"},
            provider_preferences=("OpenAI", "ollama"),
        )
        self.registry.register(spec)
        reopened = SQLiteAgentRegistry(self.database)
        self.assertEqual(reopened.get("tester"), spec)
        self.assertEqual(reopened.list_agents(), (spec,))

    def test_authorizes_only_allowlisted_capability_matched_delegation(self):
        self.registry.register(AgentSpec(
            "tester", "testing", "Creates tests",
            capabilities={"test:design"},
        ))
        self.registry.register(AgentSpec(
            "planner", "planning", "Plans work",
            capabilities={"agent:delegate"},
            delegation_targets={"tester"},
        ))
        request = DelegationRequest(
            "request-1", "task-1", "planner", "tester",
            "Create regression tests", required_capabilities={"test:design"},
        )
        self.assertEqual(self.registry.authorize_delegation(request).agent_id, "tester")

    def test_denies_unlisted_target_and_missing_capability(self):
        self.registry.register(AgentSpec("reader", "research", "Reads supplied material"))
        self.registry.register(AgentSpec(
            "planner", "planning", "Plans work",
            capabilities={"agent:delegate"},
        ))
        with self.assertRaises(PermissionError):
            self.registry.authorize_delegation(
                DelegationRequest("r1", "t1", "planner", "reader", "Delegate")
            )

    def test_rejects_unknown_targets_and_duplicate_ids(self):
        with self.assertRaises(ValueError):
            self.registry.register(AgentSpec(
                "planner", "planning", "Plans work",
                capabilities={"agent:delegate"},
                delegation_targets={"missing"},
            ))
        self.registry.register(AgentSpec("reader", "research", "Reads material"))
        with self.assertRaises(ValueError):
            self.registry.register(AgentSpec("reader", "research", "Duplicate"))

    def test_default_registry_has_no_product_level_agent_cap(self):
        registry = SQLiteAgentRegistry(self.database)
        for index in range(125):
            registry.register(AgentSpec(f"agent-{index}", "research", "Research"))
        self.assertEqual(len(registry.list_agents()), 125)

    def test_enforces_capacity(self):
        for name in ("one", "two", "three"):
            self.registry.register(AgentSpec(name, "research", name))
        with self.assertRaises(ValueError):
            self.registry.register(AgentSpec("four", "research", "four"))


if __name__ == "__main__":
    unittest.main()
