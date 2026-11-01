import unittest

from automation.agent_coordinator import SpecialistAgentCoordinator
from automation.specialist_agent_catalog import (
    default_specialist_agents,
    specialist_profile_ids,
)


class SpecialistAgentCatalogTests(unittest.TestCase):
    def test_catalog_has_twelve_unique_profiles(self):
        profiles = default_specialist_agents()
        self.assertEqual(len(profiles), 12)
        self.assertEqual(len({p.agent_id for p in profiles}), 12)
        self.assertEqual(set(specialist_profile_ids()), {p.agent_id for p in profiles})

    def test_profiles_satisfy_coordinator_contract(self):
        for profile in default_specialist_agents():
            SpecialistAgentCoordinator._validate_agent(profile)
            self.assertEqual(profile.action, "emit")
            self.assertTrue(profile.enabled)

    def test_profiles_do_not_receive_privileged_execution_permissions(self):
        forbidden = {"shell:execute", "repository:write", "deploy:production",
                     "secrets:read", "workflow:merge", "admin"}
        for profile in default_specialist_agents():
            self.assertFalse(profile.permissions & forbidden)

    def test_catalog_returns_fresh_immutable_profiles(self):
        first = default_specialist_agents()
        second = default_specialist_agents()
        self.assertIsNot(first, second)
        self.assertIsNot(first[0], second[0])
        with self.assertRaises(AttributeError):
            first[0].agent_id = "mutated"


if __name__ == "__main__":
    unittest.main()
