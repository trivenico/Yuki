"""Tests for Yuki's curated 21-agent starter fleet."""
import tempfile
import unittest
from pathlib import Path

from automation.default_agents import seed_starter_agents, starter_agent_specs
from automation.sqlite_agent_registry import SQLiteAgentRegistry


class StarterAgentFleetTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.registry = SQLiteAgentRegistry(str(Path(self.temp_dir.name) / "yuki.db"))

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_defines_twenty_unique_profiles_on_supported_roles(self):
        specs = starter_agent_specs()
        self.assertEqual(len(specs), 100)
        self.assertEqual(len({spec.agent_id for spec in specs}), 100)
        self.assertTrue(all(spec.role in {"research", "coding", "testing", "reporting"} for spec in specs))
        self.assertTrue(all(spec.allow_cloud is False for spec in specs))
        self.assertEqual(specs[-1].agent_id, "yuki-coordinator")
        self.assertEqual(len(specs[-1].delegation_targets), 99)

    def test_seeds_persistently_and_is_idempotent(self):
        inserted = seed_starter_agents(self.registry)
        self.assertEqual(len(inserted), 100)
        self.assertEqual(len(self.registry.list_agents()), 100)
        self.assertEqual(seed_starter_agents(self.registry), ())

    def test_rejects_conflicting_existing_profile(self):
        from core.agent_contracts import AgentSpec
        self.registry.register(AgentSpec(
            "research-analyst", "research", "Conflicting profile",
            capabilities={"research:analyze"},
        ))
        with self.assertRaises(ValueError):
            seed_starter_agents(self.registry)


if __name__ == "__main__":
    unittest.main()
