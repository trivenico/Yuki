"""Integration tests using the real built-in skill registry and Skill API."""
from __future__ import annotations

import unittest
from unittest.mock import patch

import yuki
from skills.registry import SkillRegistry


class RealSkillRuntimeIntegrationTests(unittest.TestCase):
    def setUp(self):
        yuki._APPROVED_SKILL_RUNS.clear()
        self.audit = patch.object(yuki, "write_audit_event")
        self.audit.start()
        self.addCleanup(self.audit.stop)
        self.addCleanup(yuki._APPROVED_SKILL_RUNS.clear)

    def test_time_skill_is_registered_and_invoked_through_execute(self):
        registry = SkillRegistry.defaults(".")
        skill = registry.get("time")
        self.assertIsNotNone(skill)
        self.assertTrue(callable(getattr(skill, "execute", None)))

        with patch.object(yuki.SkillRegistry, "defaults", return_value=registry):
            blocked = yuki.handle_command("/skills run time", [], [], None, None)
            self.assertIn("Approval required", blocked)

            approved = yuki.handle_command("/skills approve time", [], [], None, None)
            self.assertIn("approved for this process", approved)

            result = yuki.handle_command("/skills run time", [], [], None, None)
        self.assertTrue(result)
        self.assertNotIn("Approval required", result)
        self.assertEqual(yuki._APPROVED_SKILL_RUNS, set())

    def test_registry_rejects_unknown_skill_without_fallback_import(self):
        registry = SkillRegistry.defaults(".")
        with patch.object(yuki.SkillRegistry, "defaults", return_value=registry):
            result = yuki.handle_command("/skills run not_registered arbitrary", [], [], None, None)
        self.assertIn("No registered skill named", result)


if __name__ == "__main__":
    unittest.main()
