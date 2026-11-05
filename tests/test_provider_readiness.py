import unittest

from providers.provider_readiness import classify_readiness


class ProviderReadinessClassificationTests(unittest.TestCase):
    def test_unconfigured_provider_is_not_configured(self):
        self.assertEqual(classify_readiness(
            provider_name="ollama", configured=False, reachable=None,
        ), "not_configured")

    def test_unreachable_provider_is_unavailable(self):
        self.assertEqual(classify_readiness(
            provider_name="ollama", configured=True, reachable=False,
        ), "unavailable")

    def test_reachable_is_not_mistaken_for_live_verified(self):
        self.assertEqual(classify_readiness(
            provider_name="ollama", configured=True, reachable=True,
        ), "reachable_unverified")

    def test_unknown_availability_remains_unverified(self):
        self.assertEqual(classify_readiness(
            provider_name="ollama", configured=True, reachable=None,
        ), "configured_unverified")

    def test_only_explicit_passing_live_report_verifies_provider(self):
        self.assertEqual(classify_readiness(
            provider_name="ollama", configured=True, reachable=True,
            live_status="pass",
        ), "live_verified")

    def test_partial_and_failed_live_reports_are_not_verified(self):
        self.assertEqual(classify_readiness(
            provider_name="ollama", configured=True, reachable=True,
            live_status="partial",
        ), "partially_verified")
        self.assertEqual(classify_readiness(
            provider_name="ollama", configured=True, reachable=True,
            live_status="fail",
        ), "live_check_failed")


if __name__ == "__main__":
    unittest.main()
