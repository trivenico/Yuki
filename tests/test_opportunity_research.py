import json
import tempfile
import unittest
from pathlib import Path

from core.opportunity_research import OpportunityResearch, OpportunityScore, validate_source_url


class OpportunityResearchTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.store = OpportunityResearch(Path(self.tempdir.name) / "research.sqlite3")
        self.opp = self.store.add_opportunity(
            "Niche spreadsheet templates", "digital_product", "freelance project managers",
            "Test whether buyers need reusable project reporting templates", 20, "USD"
        )

    def add_two_sources(self):
        self.store.add_evidence(self.opp, "official", "https://example.com/pricing",
                                "Official platform fees and seller eligibility", "2026-10-19", 4)
        self.store.add_evidence(self.opp, "independent", "https://example.org/review",
                                "Independent review discusses buyer demand and competition", "2026-10-19", 3)

    def test_new_opportunity_persists(self):
        self.assertEqual(self.store.list_opportunities()[0]["id"], self.opp)

    def test_no_evidence_cannot_be_promising(self):
        score = OpportunityScore(5, 5, 5, 5, 5, 5)
        verdict = self.store.assess(self.opp, score, "Looks attractive", "Find buyer evidence")
        self.assertEqual(verdict.verdict, "needs_research")
        self.assertEqual(verdict.evidence_count, 0)

    def test_two_sources_and_score_can_be_promising(self):
        self.add_two_sources()
        score = OpportunityScore(4, 5, 5, 4, 4, 4)
        verdict = self.store.assess(self.opp, score, "Sources support a low-cost test", "Interview three potential buyers")
        self.assertEqual(verdict.verdict, "promising")
        self.assertEqual(self.store.list_opportunities("validated")[0]["id"], self.opp)

    def test_high_risk_is_avoided(self):
        self.add_two_sources()
        score = OpportunityScore(4, 4, 4, 4, 1, 4)
        verdict = self.store.assess(self.opp, score, "Risk is too high", "Reject or redesign")
        self.assertEqual(verdict.verdict, "avoid")

    def test_rejects_non_https_and_credentials_in_url(self):
        with self.assertRaises(ValueError):
            validate_source_url("http://example.com")
        with self.assertRaises(ValueError):
            validate_source_url("https://user:password@example.com/path")
        with self.assertRaises(ValueError):
            validate_source_url("https://localhost/private")

    def test_invalid_score_rejected(self):
        with self.assertRaises(ValueError):
            OpportunityScore(9, 3, 3, 3, 3, 3)

    def test_missing_opportunity_rejected(self):
        with self.assertRaises(KeyError):
            self.store.add_evidence(999, "official", "https://example.com", "claim", "today", 3)

    def test_coach_plan_respects_time_budget(self):
        plan = self.store.coach_plan(240, energy="normal")
        self.assertEqual(sum(block["minutes"] for block in plan), 240)
        self.assertTrue(any(block["requires_approval"] for block in plan))

    def test_coach_plan_rejects_invalid_budget(self):
        with self.assertRaises(ValueError):
            self.store.coach_plan(0)

    def test_export_is_valid_json(self):
        self.add_two_sources()
        exported = json.loads(self.store.export_json())
        self.assertEqual(len(exported["evidence"]), 2)


if __name__ == "__main__":
    unittest.main()
