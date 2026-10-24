"""Tests for the skill lifecycle status command."""
from __future__ import annotations

import unittest
from unittest.mock import patch

import yuki


class FakeRegistry:
    def names(self):
        return ("calendar", "file")


class SkillLifecycleStatusTests(unittest.TestCase):
    def test_status_compares_discovery_and_registry_without_plugin_loading(self):
        with patch.object(yuki, "_static_candidates", return_value=({"calendar_skill", "new_skill"}, [])), \
             patch.object(yuki.SkillRegistry, "defaults", return_value=FakeRegistry()) as defaults, \
             patch.object(yuki, "write_audit_event") as audit:
            result = yuki.handle_command("/skills status", [], [], None, None)
        defaults.assert_called_once()
        self.assertIn("2 discovered candidate module(s)", result)
        self.assertIn("2 built-in registered skill(s)", result)
        self.assertIn("calendar, file", result)
        self.assertIn("new_skill", result)
        self.assertIn("trust policy remains explicit", result)
        audit.assert_called_once()

    def test_status_fails_closed_if_registry_cannot_be_read(self):
        with patch.object(yuki, "_static_candidates", return_value=(set(), [])), \
             patch.object(yuki.SkillRegistry, "defaults", side_effect=RuntimeError("broken")), \
             patch.object(yuki, "write_audit_event"):
            result = yuki.handle_command("/skills status", [], [], None, None)
        self.assertIn("Could not inspect", result)
        self.assertIn("No plugin modules were loaded", result)


if __name__ == "__main__":
    unittest.main()
