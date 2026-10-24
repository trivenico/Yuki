"""Tests for read-only runtime skill registry commands."""
from __future__ import annotations

import unittest
from unittest.mock import patch

import yuki


class FakeSkill:
    name = "calendar"
    description = "Calendar operations"
    risk = "medium"
    requires_approval = True


class FakeRegistry:
    def describe(self):
        return [{"name": "calendar", "description": "Calendar operations", "risk": "medium"}]

    def get(self, name):
        return FakeSkill() if name == "calendar" else None


class RuntimeSkillRegistryCommandTests(unittest.TestCase):
    def test_list_shows_registered_skills(self):
        with patch.object(yuki.SkillRegistry, "defaults", return_value=FakeRegistry()), \
             patch.object(yuki, "write_audit_event") as audit:
            result = yuki.handle_command("/skills list", [], [], None, None)
        self.assertIn("calendar [medium]", result)
        audit.assert_called_once_with("skills:list", "completed")

    def test_inspect_shows_metadata_but_never_executes(self):
        with patch.object(yuki.SkillRegistry, "defaults", return_value=FakeRegistry()), \
             patch.object(yuki, "write_audit_event") as audit:
            result = yuki.handle_command("/skills inspect calendar", [], [], None, None)
        self.assertIn("Requires approval: True", result)
        self.assertIn("inspection only", result)
        audit.assert_called_once_with("skills:inspect", "completed")

    def test_inspect_reports_unknown_skill(self):
        with patch.object(yuki.SkillRegistry, "defaults", return_value=FakeRegistry()), \
             patch.object(yuki, "write_audit_event") as audit:
            result = yuki.handle_command("/skills inspect missing", [], [], None, None)
        self.assertIn("No registered skill named", result)
        audit.assert_called_once_with("skills:inspect", "not_found")


if __name__ == "__main__":
    unittest.main()
