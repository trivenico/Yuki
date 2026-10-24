"""Tests for YukiAgent's explicit guarded skill runtime adapter."""
from __future__ import annotations

import unittest
from unittest.mock import Mock

from core.guarded_skill_runtime import SkillInvocationResult
from skills.base import SkillResult
from yuki_agent import YukiAgent


class DemoSkill:
    name = "demo"
    risk = "high"
    requires_approval = True

    def __init__(self):
        self.arguments = []

    def execute(self, argument=""):
        self.arguments.append(argument)
        return SkillResult(True, "demo completed")


class Registry:
    def __init__(self):
        self.skill = DemoSkill()

    def get(self, name):
        return self.skill if name == self.skill.name else None


class Provider:
    def is_available(self):
        return True

    def generate(self, prompt):
        return "plain provider answer"


class YukiAgentSkillAdapterTests(unittest.TestCase):
    def setUp(self):
        self.registry = Registry()
        self.audit_events = []
        self.agent = YukiAgent(Provider(), skill_registry=self.registry,
                               skill_audit=lambda action, status: self.audit_events.append((action, status)))

    def test_invocation_is_blocked_without_explicit_approval(self):
        result = self.agent.invoke_skill("demo", "payload")
        self.assertIsInstance(result, SkillInvocationResult)
        self.assertEqual(result.status, "approval_required")
        self.assertEqual(self.registry.skill.arguments, [])

    def test_approved_invocation_uses_shared_guard_and_consumes_approval(self):
        self.agent.approve_skill_once("demo")
        result = self.agent.invoke_skill("demo", "payload")
        self.assertTrue(result.ok)
        self.assertEqual(self.registry.skill.arguments, ["payload"])
        again = self.agent.invoke_skill("demo", "again")
        self.assertEqual(again.status, "approval_required")
        self.assertEqual(self.registry.skill.arguments, ["payload"])

    def test_unregistered_skill_cannot_be_approved(self):
        with self.assertRaises(ValueError):
            self.agent.approve_skill_once("not_registered")

    def test_model_text_does_not_auto_invoke_skills(self):
        self.assertEqual(self.agent.ask("please do something"), "plain provider answer")
        self.assertEqual(self.registry.skill.arguments, [])

    def test_runtime_must_be_explicitly_configured(self):
        agent = YukiAgent(Provider())
        self.assertEqual(agent.invoke_skill("demo").status, "not_configured")
        with self.assertRaises(RuntimeError):
            agent.approve_skill_once("demo")

    def test_configuring_runtime_clears_pending_approvals(self):
        self.agent.approve_skill_once("demo")
        self.agent.configure_skill_runtime(self.registry)
        result = self.agent.invoke_skill("demo")
        self.assertEqual(result.status, "approval_required")


if __name__ == "__main__":
    unittest.main()
