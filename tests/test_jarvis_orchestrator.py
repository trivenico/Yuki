import tempfile
import unittest
from pathlib import Path

from core.companion_memory import CompanionMemory
from core.earning_companion import EarningCompanion
from core.jarvis_orchestrator import ActionRisk, Capability, JarvisOrchestrator
from core.opportunity_research import OpportunityResearch


class JarvisOrchestratorTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        root = Path(self.tempdir.name)
        self.earning = EarningCompanion(root / "earning.sqlite3")
        self.memory = CompanionMemory(root / "memory.sqlite3")
        self.research = OpportunityResearch(root / "research.sqlite3")
        self.app = JarvisOrchestrator(
            earning=self.earning, memory=self.memory, research=self.research
        )

    def test_capability_manifest_does_not_claim_unavailable_features(self):
        manifest = {item["name"]: item for item in self.app.capability_manifest()}
        self.assertFalse(manifest["web_research"]["available"])
        self.assertTrue(manifest["email_outreach"]["approval_required"])
        self.assertFalse(manifest["daily_coach"]["approval_required"])

    def test_external_financial_and_device_actions_require_approval(self):
        for risk in (ActionRisk.EXTERNAL, ActionRisk.FINANCIAL, ActionRisk.DEVICE_CONTROL, ActionRisk.LOCAL_WRITE):
            proposal = self.app.propose_action(
                title="Review action", rationale="Requires a human decision",
                action_type="test", risk=risk, payload={"amount": 10},
            )
            self.assertTrue(proposal.approval_required)

    def test_read_only_proposal_does_not_require_approval(self):
        proposal = self.app.propose_action(
            title="Review source", rationale="No state change", action_type="read",
            risk=ActionRisk.READ_ONLY,
        )
        self.assertFalse(proposal.approval_required)

    def test_invalid_proposal_rejected(self):
        with self.assertRaises(ValueError):
            self.app.propose_action(title="", rationale="x", action_type="read", risk=ActionRisk.READ_ONLY)

    def test_daily_briefing_includes_plan_and_truthful_notices(self):
        self.earning.add_goal("First client", 500)
        self.memory.remember("goal", "Build a freelance portfolio", confirmed=True)
        self.research.add_opportunity(
            "Offer landing page audits", "freelance", "small businesses",
            "Test demand for quick website audits", 0,
        )
        briefing = self.app.daily_briefing(available_minutes=240)
        self.assertEqual(briefing.earning["active_goals"], 1)
        self.assertEqual(len(briefing.plan), 5)
        self.assertEqual(sum(block["minutes"] for block in briefing.plan), 240)
        self.assertEqual(len(briefing.memories), 1)
        self.assertTrue(any("does not imply live web research" in notice for notice in briefing.notices))
        self.assertEqual(len(briefing.proposals), 1)

    def test_daily_briefing_respects_memory_category(self):
        self.memory.remember("goal", "Find first client", confirmed=True)
        self.memory.remember("preference", "Short daily brief", confirmed=True)
        briefing = self.app.daily_briefing(memory_category="goal")
        self.assertEqual(len(briefing.memories), 1)
        self.assertEqual(briefing.memories[0]["category"], "goal")

    def test_invalid_plan_budget_fails_fast(self):
        with self.assertRaises(ValueError):
            self.app.daily_briefing(available_minutes=0)

    def test_capabilities_can_be_overridden_explicitly(self):
        custom = JarvisOrchestrator(
            earning=self.earning, memory=self.memory, research=self.research,
            capabilities=[Capability("custom", "test", "test capability", True, ActionRisk.READ_ONLY, False)],
        )
        self.assertEqual([item["name"] for item in custom.capability_manifest()], ["custom"])


if __name__ == "__main__":
    unittest.main()
