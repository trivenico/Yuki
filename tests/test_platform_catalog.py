import unittest
from datetime import date

from core.platform_catalog import build_platform_registry


class ReviewedPlatformCatalogTests(unittest.TestCase):
    def test_upwork_profile_is_visible_but_all_execution_is_disabled(self):
        registry = build_platform_registry()
        platform = registry.get("upwork")
        self.assertIsNotNone(platform)
        self.assertEqual(platform.canonical_domains, ("upwork.com", "mcp.upwork.com"))
        self.assertEqual(platform.supported_regions, ())
        self.assertFalse(platform.enabled)
        self.assertEqual(
            registry.effective_mode("upwork", "read_opportunities", date(2026, 11, 11)),
            "unsupported",
        )

    def test_upwork_mutating_actions_remain_unsupported(self):
        registry = build_platform_registry()
        for action in (
            "submit_proposal", "send_message", "accept_contract",
            "fund_escrow", "approve_milestone", "withdraw",
            "change_payout_details",
        ):
            self.assertEqual(
                registry.effective_mode("upwork", action, date(2026, 11, 11)),
                "unsupported",
            )

    def test_upwork_proposal_can_only_be_drafted(self):
        registry = build_platform_registry()
        self.assertEqual(
            registry.effective_mode("upwork", "draft_proposal", date(2026, 11, 11)),
            "unsupported",
        )
        # The platform remains disabled; its declared action metadata does not
        # grant any capability until a separately reviewed enablement decision.
        self.assertEqual(
            registry.get("upwork").action("draft_proposal").automation_mode,
            "draft_only",
        )

    def test_policy_review_expires_and_requires_reassessment(self):
        platform = build_platform_registry().get("upwork")
        self.assertTrue(platform.policy_is_current(date(2026, 11, 18)))
        self.assertFalse(platform.policy_is_current(date(2026, 11, 19)))


if __name__ == "__main__":
    unittest.main()
