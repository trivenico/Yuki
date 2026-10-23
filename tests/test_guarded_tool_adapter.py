"""Tests for adapting existing Yuki tools to the centralized guarded runtime."""
from __future__ import annotations

import unittest

from core.guarded_skill_runtime import OneShotSkillApprovals, invoke_registered_skill
from core.guarded_tool_adapter import GuardedToolRegistry


class GuardedToolAdapterTests(unittest.TestCase):
    def setUp(self):
        self.registry = GuardedToolRegistry()
        self.approvals = OneShotSkillApprovals()
        self.audit_events = []

    def invoke(self, name, argument=""):
        return invoke_registered_skill(
            self.registry, name, argument, approvals=self.approvals,
            audit=lambda action, status: self.audit_events.append((action, status)),
        )

    def test_safe_calculator_tool_uses_shared_guard(self):
        result = self.invoke("calculate", "12 * (3 + 4)")
        self.assertTrue(result.ok)
        self.assertEqual(result.message, "84")

    def test_save_note_requires_approval(self):
        result = self.invoke("save_note", "private test note")
        self.assertEqual(result.status, "approval_required")

    def test_approved_save_note_runs_once(self):
        self.approvals.approve("save_note")
        # Patch the allow-listed function so this test never writes to disk.
        from unittest.mock import patch
        with patch("core.guarded_tool_adapter.TOOLS", {"save_note": lambda text: "saved"}):
            registry = GuardedToolRegistry()
            self.approvals.approve("save_note")
            first = invoke_registered_skill(registry, "save_note", "hello", approvals=self.approvals)
            second = invoke_registered_skill(registry, "save_note", "again", approvals=self.approvals)
        self.assertTrue(first.ok)
        self.assertEqual(second.status, "approval_required")

    def test_unknown_tool_is_not_discovered(self):
        result = self.invoke("arbitrary_python")
        self.assertEqual(result.status, "not_found")

    def test_registry_names_are_allowlisted(self):
        self.assertEqual(self.registry.names(), ("calculate", "list_files", "save_note", "time"))


if __name__ == "__main__":
    unittest.main()
