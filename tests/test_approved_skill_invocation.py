"""Tests for guarded skill invocation from Yuki's terminal."""
from __future__ import annotations

import unittest
from unittest.mock import patch

import yuki
from skills.base import SkillResult


class FakeSkill:
    name = "dangerous_demo"
    description = "Test-only skill"
    risk = "high"
    requires_approval = True

    def __init__(self):
        self.calls = []

    def execute(self, argument=""):
        self.calls.append(argument)
        return SkillResult(True, "invoked safely")


class FakeRegistry:
    def __init__(self, skill):
        self.skill = skill

    def get(self, name):
        return self.skill if name == self.skill.name else None


class ApprovedSkillInvocationTests(unittest.TestCase):
    def setUp(self):
        yuki._APPROVED_SKILL_RUNS.clear()
        self.skill = FakeSkill()
        self.registry = FakeRegistry(self.skill)
        self.patcher = patch.object(yuki.SkillRegistry, "defaults", return_value=self.registry)
        self.patcher.start()
        self.audit = patch.object(yuki, "write_audit_event")
        self.audit_mock = self.audit.start()
        self.addCleanup(self.patcher.stop)
        self.addCleanup(self.audit.stop)
        self.addCleanup(yuki._APPROVED_SKILL_RUNS.clear)

    def test_requires_approval_before_invocation(self):
        result = yuki.handle_command("/skills run dangerous_demo hello", [], [], None, None)
        self.assertIn("Approval required", result)
        self.assertEqual(self.skill.calls, [])

    def test_approval_is_one_shot_and_invocation_uses_execute(self):
        approval = yuki.handle_command("/skills approve dangerous_demo", [], [], None, None)
        self.assertIn("approved for this process", approval)
        result = yuki.handle_command("/skills run dangerous_demo hello world", [], [], None, None)
        self.assertEqual(result, "invoked safely")
        self.assertEqual(self.skill.calls, ["hello world"])
        again = yuki.handle_command("/skills run dangerous_demo second", [], [], None, None)
        self.assertIn("Approval required", again)
        self.assertEqual(self.skill.calls, ["hello world"])

    def test_unknown_skill_is_not_invoked(self):
        result = yuki.handle_command("/skills run missing payload", [], [], None, None)
        self.assertIn("No registered skill named", result)
        self.assertEqual(self.skill.calls, [])


if __name__ == "__main__":
    unittest.main()
