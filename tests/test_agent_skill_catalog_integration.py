"""Tests for task-driven skill discovery in YukiCoreAgent."""
import unittest

from core.agent import YukiCoreAgent
from core.permissions import PermissionGate
from skills.base import Skill
from skills.registry import SkillRegistry


class DemoSkill(Skill):
    name = "workspace_search"
    description = "Search project files and research workspace documents"
    risk = "low"
    tags = ("files", "search", "research")
    usage = "Pass a query to find relevant workspace documents"
    agent_guidance = "Search narrowly, verify evidence, and summarize the result."
    requires_approval = False
    def execute(self, argument=""):
        return {"ok": True, "message": "searched:" + argument}


class Gate:
    def __init__(self, allowed=True):
        self.allowed = allowed
        self.requests = []
    def request(self, **kwargs):
        self.requests.append(kwargs)
        return self.allowed


class AgentSkillCatalogIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.skill = DemoSkill()
        self.gate = Gate(True)
        self.agent = YukiCoreAgent(registry=SkillRegistry([self.skill]), permission_gate=self.gate)

    def test_discovery_finds_skill_without_execution(self):
        cards = self.agent.discover_skills("search workspace files")
        self.assertEqual(cards[0].name, "workspace_search")
        self.assertFalse(hasattr(self.skill, "executed"))

    def test_task_selection_invokes_through_permission_gate(self):
        result = self.agent.run_skill_for_task("research workspace files", "deployment guide")
        self.assertTrue(result.ok)
        self.assertEqual(result.tool_name, "workspace_search")
        self.assertEqual(result.message, "searched:deployment guide")
        self.assertEqual(self.gate.requests[0]["action"], "skill:workspace_search")

    def test_no_match_fails_closed_without_approval_or_execution(self):
        result = self.agent.run_skill_for_task("bake sourdough bread", "recipe")
        self.assertFalse(result.ok)
        self.assertEqual(result.stage, "skill_not_found")
        self.assertEqual(self.gate.requests, [])

    def test_denied_approval_prevents_execution(self):
        self.gate.allowed = False
        result = self.agent.run_skill_for_task("search workspace files", "secret")
        self.assertFalse(result.ok)
        self.assertEqual(result.stage, "permission_denied")

    def test_empty_query_fails_closed(self):
        result = self.agent.run_skill_for_task("   ", "")
        self.assertFalse(result.ok)
        self.assertEqual(result.stage, "skill_not_found")


if __name__ == "__main__":
    unittest.main()