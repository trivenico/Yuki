"""Tests for the read-only /skill inspect command."""
from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

import yuki


class FakeSkill:
    name = "weather"
    description = "Current weather lookup"
    risk = "low"
    requires_approval = False


class FakeRegistry:
    def __init__(self, skill=None):
        self.skill = skill

    def describe(self):
        return []

    def get(self, name):
        return self.skill if self.skill is not None and name == self.skill.name else None


class FakeApprovalStore:
    pass


class SkillInspectCommandTests(unittest.TestCase):
    def invoke(self, command, skill=None):
        registry = FakeRegistry(skill)
        with (
            patch.object(yuki.SkillRegistry, "defaults", return_value=registry),
            patch.object(yuki, "_get_skill_approval_store", return_value=FakeApprovalStore()),
        ):
            return yuki.handle_command(command, [], [], None, None)

    def test_inspect_returns_metadata_without_invoking_skill(self):
        skill = FakeSkill()
        skill.execute = Mock(return_value="must not run")
        result = self.invoke("/skill inspect weather", skill)
        self.assertIn("Name: weather", result)
        self.assertIn("Description: Current weather lookup", result)
        self.assertIn("Risk: low", result)
        self.assertIn("Invocation approval: not required", result)
        skill.execute.assert_not_called()

    def test_inspect_unknown_skill_returns_safe_message(self):
        self.assertEqual(self.invoke("/skill inspect missing", FakeSkill()), "Unknown skill.")

    def test_inspect_requires_name(self):
        self.assertEqual(self.invoke("/skill inspect"), "Usage: /skill inspect NAME")

    def test_unknown_risk_fails_closed_for_approval_metadata(self):
        skill = FakeSkill()
        skill.risk = "custom"
        skill.requires_approval = False
        result = self.invoke("/skill inspect weather", skill)
        self.assertIn("Invocation approval: required", result)

    def test_description_newlines_are_flattened_and_bounded(self):
        skill = FakeSkill()
        skill.description = "first line\\nsecond line"
        result = self.invoke("/skill inspect weather", skill)
        self.assertIn("Description: first line second line", result)
        self.assertNotIn("second line\\n", result)


if __name__ == "__main__":
    unittest.main()
