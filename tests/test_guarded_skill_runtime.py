"""Tests for the centralized guarded skill invocation service."""
from __future__ import annotations

import unittest

from core.guarded_skill_runtime import OneShotSkillApprovals, invoke_registered_skill
from skills.base import SkillResult


class FakeSkill:
    name = "demo"
    risk = "high"
    requires_approval = True

    def __init__(self, result=None):
        self.calls = []
        self.result = result or SkillResult(True, "done")

    def execute(self, argument=""):
        self.calls.append(argument)
        return self.result


class FakeRegistry:
    def __init__(self, skill):
        self.skill = skill

    def get(self, name):
        return self.skill if name == self.skill.name else None


class GuardedSkillRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.skill = FakeSkill()
        self.registry = FakeRegistry(self.skill)
        self.approvals = OneShotSkillApprovals()
        self.audit_events = []

    def audit(self, action, status):
        self.audit_events.append((action, status))

    def invoke(self, name="demo", argument="hello"):
        return invoke_registered_skill(
            self.registry, name, argument,
            approvals=self.approvals, audit=self.audit,
        )

    def test_blocks_without_approval_and_does_not_call_skill(self):
        result = self.invoke()
        self.assertFalse(result.ok)
        self.assertEqual(result.status, "approval_required")
        self.assertEqual(self.skill.calls, [])

    def test_approval_is_consumed_before_exactly_one_invocation(self):
        self.approvals.approve("demo")
        first = self.invoke(argument="payload")
        second = self.invoke(argument="payload2")
        self.assertTrue(first.ok)
        self.assertEqual(self.skill.calls, ["payload"])
        self.assertEqual(second.status, "approval_required")
        self.assertEqual(self.audit_events[-1], ("skill:invoke", "approval_required"))

    def test_unknown_skill_and_oversized_argument_fail_closed(self):
        self.assertEqual(self.invoke(name="unknown").status, "not_found")
        self.assertEqual(self.invoke(argument="x" * 20001).status, "invalid_argument")
        self.assertEqual(self.skill.calls, [])

    def test_audit_does_not_contain_arguments_or_result(self):
        self.approvals.approve("demo")
        self.invoke(argument="private-user-content")
        self.assertNotIn("private-user-content", repr(self.audit_events))
        self.assertNotIn("done", repr(self.audit_events))

    def test_failed_skill_result_is_preserved_as_failure(self):
        self.skill.result = SkillResult(False, "operation refused")
        self.approvals.approve("demo")
        result = self.invoke()
        self.assertFalse(result.ok)
        self.assertEqual(result.status, "skill_failed")


if __name__ == "__main__":
    unittest.main()
