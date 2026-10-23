"""CLI integration tests proving existing tool commands use the guarded runtime."""
from __future__ import annotations

import unittest
from unittest.mock import patch

import yuki
from yuki_security import SecurityPolicy


class GuardedToolCliTests(unittest.TestCase):
    def setUp(self):
        yuki._TOOL_APPROVALS.clear()
        self.original_registry = yuki._TOOL_REGISTRY
        self.addCleanup(setattr, yuki, "_TOOL_REGISTRY", self.original_registry)
        self.policy = SecurityPolicy(allowed_tools=yuki.TOOLS.keys())
        self.agent = object()
        self.providers = object()
        self.memory = []
        self.tasks = []

    def command(self, text):
        return yuki.handle_command(text, self.memory, self.tasks, self.agent, self.providers, self.policy)

    def test_calculator_runs_through_guarded_adapter(self):
        result = self.command("/tool calculate 12 * (3 + 4)")
        self.assertEqual(result, "84")

    def test_save_note_requires_approval_before_execution(self):
        with patch("core.guarded_tool_adapter.TOOLS", {"save_note": lambda value: "saved"}):
            yuki._TOOL_REGISTRY = yuki.GuardedToolRegistry()
            result = self.command("/tool save_note private")
        self.assertIn("approval", result.lower())

    def test_approval_command_is_explicit_and_one_shot(self):
        with patch("core.guarded_tool_adapter.TOOLS", {"save_note": lambda value: "saved"}):
            yuki._TOOL_REGISTRY = yuki.GuardedToolRegistry()
            self.assertIn("approved", self.command("/tool approve save_note").lower())
            self.assertEqual(self.command("/tool save_note hello"), "saved")
            self.assertIn("approval", self.command("/tool save_note again").lower())

    def test_security_policy_still_blocks_tool(self):
        restrictive = SecurityPolicy(allowed_tools=[])
        result = yuki.handle_command("/tool calculate 1+1", self.memory, self.tasks, self.agent, self.providers, restrictive)
        self.assertIn("blocked", result.lower())


if __name__ == "__main__":
    unittest.main()
