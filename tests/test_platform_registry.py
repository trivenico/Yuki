import unittest
from datetime import date, timedelta

from core.platform_registry import (
    PlatformAction,
    PlatformCapability,
    PlatformRegistry,
)


class PlatformRegistryTests(unittest.TestCase):
    def reviewed_platform(self, **overrides):
        values = {
            "platform_id": "sample-market",
            "display_name": "Sample Market",
            "canonical_domains": ("market.example",),
            "categories": ("freelance", "digital_products"),
            "supported_regions": ("GB",),
            "actions": (
                PlatformAction(
                    "read_opportunities",
                    automation_mode="official_api",
                    required_scopes=("opportunities.read",),
                    owner_approval_required=False,
                ),
                PlatformAction(
                    "submit_proposal",
                    automation_mode="official_api",
                    required_scopes=("proposals.write",),
                ),
                PlatformAction(
                    "draft_listing",
                    automation_mode="draft_only",
                    owner_approval_required=False,
                ),
            ),
            "policy_url": "https://market.example/terms",
            "policy_reviewed_on": date(2026, 1, 1),
            "policy_recheck_by": date(2027, 1, 1),
            "adapter_version": "1.0",
            "enabled": True,
            "credential_reference": "secret://platforms/sample-market/oauth",
        }
        values.update(overrides)
        return PlatformCapability(**values)

    def test_current_policy_enables_only_declared_action_modes(self):
        registry = PlatformRegistry([self.reviewed_platform()])
        self.assertEqual(
            registry.effective_mode("sample-market", "read_opportunities", date(2026, 6, 1)),
            "official_api",
        )
        self.assertEqual(
            registry.effective_mode("sample-market", "draft_listing", date(2026, 6, 1)),
            "draft_only",
        )
        self.assertEqual(
            registry.effective_mode("sample-market", "unknown_action", date(2026, 6, 1)),
            "unsupported",
        )

    def test_missing_stale_or_future_policy_fails_closed(self):
        platform = self.reviewed_platform(policy_recheck_by=date(2026, 5, 1))
        registry = PlatformRegistry([platform])
        self.assertEqual(registry.effective_mode("sample-market", "read_opportunities", date(2026, 6, 1)), "unsupported")
        future = self.reviewed_platform(policy_reviewed_on=date(2026, 7, 1))
        self.assertFalse(future.policy_is_current(date(2026, 6, 1)))
        no_policy = self.reviewed_platform(policy_url=None, policy_reviewed_on=None, policy_recheck_by=None)
        self.assertEqual(no_policy.effective_mode("read_opportunities", date(2026, 6, 1)), "unsupported")

    def test_unknown_or_disabled_platform_is_unsupported(self):
        registry = PlatformRegistry([self.reviewed_platform(enabled=False)])
        self.assertEqual(registry.effective_mode("unknown", "read_opportunities", date(2026, 6, 1)), "unsupported")
        self.assertEqual(registry.effective_mode("sample-market", "read_opportunities", date(2026, 6, 1)), "unsupported")

    def test_mutating_actions_cannot_disable_owner_approval(self):
        with self.assertRaisesRegex(ValueError, "requires_owner_approval"):
            PlatformAction("publish_listing", "official_api", owner_approval_required=False)
        with self.assertRaisesRegex(ValueError, "requires_owner_approval"):
            PlatformAction("withdraw", "official_api", owner_approval_required=False)

    def test_credentials_must_be_opaque_secret_references(self):
        with self.assertRaisesRegex(ValueError, "secret_store_reference"):
            self.reviewed_platform(credential_reference="plain-token-value")

    def test_invalid_domains_and_duplicate_ids_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "invalid_platform_domains"):
            self.reviewed_platform(canonical_domains=("https://market.example/path",))
        platform = self.reviewed_platform()
        with self.assertRaisesRegex(ValueError, "duplicate_platform_id"):
            PlatformRegistry([platform, platform])

    def test_registry_is_read_only_and_unknown_actions_default_closed(self):
        registry = PlatformRegistry([self.reviewed_platform()])
        self.assertIsNone(registry.get("not-installed"))
        self.assertEqual(len(registry.list()), 1)
        self.assertIsNone(registry.get("sample-market").action("publish_listing"))


if __name__ == "__main__":
    unittest.main()
