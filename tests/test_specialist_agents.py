import unittest

from core.specialist_agents import AGENT_CATALOG, get_agent, list_agents, list_families


class SpecialistAgentCatalogTests(unittest.TestCase):
    def test_catalog_contains_exactly_100_unique_roles(self):
        ids = [agent.agent_id for agent in AGENT_CATALOG]
        self.assertEqual(len(ids), 100)
        self.assertEqual(len(set(ids)), 100)

    def test_ten_families_are_present(self):
        self.assertEqual(len(list_families()), 10)
        self.assertEqual({len(list_agents(family=family)) for family in list_families()}, {10})

    def test_each_family_has_expected_roles(self):
        for family in list_families():
            self.assertEqual(len(list_agents(family=family)), 10)

    def test_lookup_is_case_insensitive_and_unknown_safe(self):
        self.assertEqual(get_agent("CORE.ORCHESTRATOR").agent_id, "core.orchestrator")
        self.assertIsNone(get_agent("not.a.real.agent"))
        self.assertIsNone(get_agent(None))

    def test_catalog_entries_do_not_grant_tools(self):
        self.assertTrue(all(agent.allowed_tools == () for agent in AGENT_CATALOG))
        self.assertTrue(all(agent.requires_approval for agent in AGENT_CATALOG))


if __name__ == "__main__":
    unittest.main()
