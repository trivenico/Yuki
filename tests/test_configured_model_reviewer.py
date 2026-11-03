import unittest
from unittest.mock import patch

from automation.configured_model_reviewer import (
    ConfiguredModelReviewerError,
    create_configured_model_reviewer,
)


class ReadyProvider:
    def generate(self, prompt):
        return '{"verdict":"approve","findings":[],"rationale_code":"review_ok"}'
    def is_available(self):
        return True


class UnavailableProvider(ReadyProvider):
    def is_available(self):
        return False


class ConfiguredModelReviewerTests(unittest.TestCase):
    @patch("providers.factory.create_provider", return_value=ReadyProvider())
    def test_builds_adapter_from_configured_provider(self, factory):
        adapter = create_configured_model_reviewer(
            reviewer_id="review-model", config={"provider": "ollama"}, environ={}
        )
        self.assertEqual(adapter.reviewer_id, "ai:review-model")
        factory.assert_called_once_with(config={"provider": "ollama"}, environ={})

    @patch("providers.factory.create_provider", side_effect=ValueError("bad provider"))
    def test_factory_errors_fail_closed(self, factory):
        with self.assertRaises(ConfiguredModelReviewerError):
            create_configured_model_reviewer(reviewer_id="review-model")

    @patch("providers.factory.create_provider")
    def test_mock_provider_is_rejected(self, factory):
        from providers.mock import MockProvider
        factory.return_value = MockProvider()
        with self.assertRaisesRegex(ConfiguredModelReviewerError, "test-only"):
            create_configured_model_reviewer(reviewer_id="review-model")

    @patch("providers.factory.create_provider", return_value=UnavailableProvider())
    def test_unavailable_provider_is_rejected_without_fallback(self, factory):
        with self.assertRaisesRegex(ConfiguredModelReviewerError, "not ready"):
            create_configured_model_reviewer(reviewer_id="review-model")
        factory.assert_called_once()

    @patch("providers.factory.create_provider", return_value=object())
    def test_provider_without_readiness_contract_is_rejected(self, factory):
        with self.assertRaisesRegex(ConfiguredModelReviewerError, "readiness"):
            create_configured_model_reviewer(reviewer_id="review-model")

    def test_reviewer_identity_is_required(self):
        with self.assertRaises(ValueError):
            create_configured_model_reviewer(reviewer_id=" ")

    def test_config_types_are_validated(self):
        with self.assertRaises(TypeError):
            create_configured_model_reviewer(reviewer_id="review-model", config=[])


if __name__ == "__main__":
    unittest.main()
