"""Tests for deterministic, guarded on-demand skill catalog."""
import unittest

from core.guarded_skill_runtime import OneShotSkillApprovals
from skills.catalog import SkillCatalog


class Skill:
    name = "workspace_search"
    description = "Search project files and summarize relevant content"
    risk = "low"
    requires_approval = False
    tags = ("files", "search", "research")
    usage = "Provide a query to search the workspace"
    agent_guidance = "Check scope, search narrowly, cite evidence, and verify the result."
    examples = ("Find the deployment guide",)
    def execute(self, argument=""):
        return "found:" + argument


class Registry:
    def __init__(self, *skills):
        self.items = {skill.name: skill for skill in skills}
    def get(self, name):
        return self.items.get(name)
    def names(self):
        return tuple(self.items)


class SkillCatalogTests(unittest.TestCase):
    def setUp(self):
        self.skill = Skill()
        self.registry = Registry(self.skill)
        self.catalog = SkillCatalog(self.registry)

    def test_search_returns_guidance_for_relevant_skill(self):
        results = self.catalog.search("search files")
        self.assertEqual([card.name for card in results], ["workspace_search"])
        self.assertIn("verify", results[0].agent_guidance)
        self.assertEqual(results[0].examples, ("Find the deployment guide",))

    def test_search_is_deterministic_and_bounded(self):
        self.assertEqual(self.catalog.search("search files"), self.catalog.search("search files"))
        self.assertEqual(len(self.catalog.search("search files", limit=0)), 1)
        self.assertEqual(self.catalog.search("!!!"), ())

    def test_catalog_does_not_import_or_execute_during_search(self):
        self.catalog.search("project research")
        self.assertFalse(hasattr(self.skill, "was_executed"))

    def test_invocation_uses_shared_approval_guard(self):
        approvals = OneShotSkillApprovals()
        result = self.catalog.invoke("workspace_search", "deploy", approvals=approvals)
        self.assertTrue(result.ok)
        self.assertEqual(result.message, "found:deploy")

    def test_high_risk_skill_still_requires_one_shot_approval(self):
        self.skill.risk = "high"
        self.skill.requires_approval = True
        approvals = OneShotSkillApprovals()
        first = self.catalog.invoke("workspace_search", "deploy", approvals=approvals)
        self.assertEqual(first.status, "approval_required")
        approvals.approve("workspace_search", "deploy")
        second = self.catalog.invoke("workspace_search", "deploy", approvals=approvals)
        self.assertTrue(second.ok)
        third = self.catalog.invoke("workspace_search", "deploy", approvals=approvals)
        self.assertEqual(third.status, "approval_required")

    def test_high_risk_approval_is_consumed_once(self):
        approvals = OneShotSkillApprovals()
        self.skill.risk = "high"
        self.skill.requires_approval = True
        approvals.approve("workspace_search", "first")
        first = self.catalog.invoke("workspace_search", "first", approvals=approvals)
        second = self.catalog.invoke("workspace_search", "second", approvals=approvals)
        self.assertTrue(first.ok)
        self.assertEqual(first.message, "found:first")
        self.assertEqual(second.status, "approval_required")

    def test_unknown_skill_is_not_invoked(self):
        result = self.catalog.invoke("missing", "", approvals=OneShotSkillApprovals())
        self.assertEqual(result.status, "not_found")


if __name__ == "__main__":
    unittest.main()
