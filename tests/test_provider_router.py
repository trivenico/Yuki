"""Tests for deterministic local-first provider routing."""
import unittest

from providers.provider_registry import ProviderRegistry
from providers.provider_router import ProviderRouter


class ProviderRouterTests(unittest.TestCase):
    def setUp(self):
        self.registry = ProviderRegistry()
        self.registry.register(
            provider_id="local-z", version="1.0.0", privacy_class="local",
            capabilities={"chat", "vision"}, factory=lambda: None,
        )
        self.registry.register(
            provider_id="local-a", version="1.0.0", privacy_class="local",
            capabilities={"chat"}, factory=lambda: None,
        )
        self.registry.register(
            provider_id="cloud", version="1.0.0", privacy_class="cloud_provider",
            capabilities={"chat", "vision"}, factory=lambda: None,
        )
        self.router = ProviderRouter(self.registry)
        self.ready = {
            key: {"status": "live_verified", "verification_level": "live", "reachable": True}
            for key in ("local-z", "local-a", "cloud")
        }

    def test_prefers_local_and_uses_deterministic_tie_break(self):
        result = self.router.select(required_capabilities={"chat"}, readiness=self.ready)
        self.assertEqual(result.provider_id, "local-a")
        self.assertEqual(result.reason, "local_first")

    def test_cloud_is_never_selected_without_explicit_opt_in(self):
        only_cloud = {"cloud": self.ready["cloud"]}
        with self.assertRaisesRegex(LookupError, "no_provider"):
            self.router.select(required_capabilities={"chat"}, readiness=only_cloud)

    def test_cloud_can_be_selected_only_with_explicit_policy_and_opt_in(self):
        result = self.router.select(
            required_capabilities={"vision"}, readiness={"cloud": self.ready["cloud"]},
            privacy_policy="cloud_opt_in", cloud_opt_in=True,
        )
        self.assertEqual(result.provider_id, "cloud")
        self.assertEqual(result.reason, "explicit_cloud_opt_in")

    def test_unverified_or_unreachable_providers_are_excluded(self):
        evidence = {"local-a": {"status": "reachable_unverified",
                                "verification_level": "structural_only",
                                "reachable": True}}
        with self.assertRaisesRegex(LookupError, "no_provider"):
            self.router.select(required_capabilities={"chat"}, readiness=evidence)

    def test_capability_mismatch_is_excluded(self):
        with self.assertRaisesRegex(LookupError, "no_provider"):
            self.router.select(required_capabilities={"speech_input"}, readiness=self.ready)

    def test_unknown_privacy_is_excluded(self):
        self.registry.register(
            provider_id="unknown", version="1.0.0", privacy_class="unknown",
            capabilities={"chat"}, factory=lambda: None,
        )
        evidence = {"unknown": self.ready["local-a"]}
        with self.assertRaisesRegex(LookupError, "no_provider"):
            self.router.select(required_capabilities={"chat"}, readiness=evidence)

    def test_conflicting_policy_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "conflicts"):
            self.router.select(required_capabilities={"chat"}, readiness=self.ready,
                               privacy_policy="local_only", cloud_opt_in=True)


if __name__ == "__main__":
    unittest.main()
