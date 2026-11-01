import unittest

from providers.openai_responses import OpenAIResponsesProvider
from yuki_providers import ProviderManager


class OpenAIResponsesRegistryTests(unittest.TestCase):
    def test_manager_can_select_responses_provider(self):
        manager = ProviderManager(environ={
            "OPENAI_API_KEY": "test-only-key",
            "YUKI_OPENAI_RESPONSES_MODEL": "gpt-6-astra",
        })
        self.assertIn("openai_responses", {p["name"] for p in manager.list_providers()})
        self.assertEqual(manager.select("openai_responses"), "Selected provider: openai_responses")
        self.assertIsInstance(manager.create_selected_adapter(), OpenAIResponsesProvider)

    def test_catalog_reports_readiness_without_exposing_key(self):
        manager = ProviderManager(environ={
            "OPENAI_API_KEY": "sensitive-test-key",
            "YUKI_OPENAI_RESPONSES_MODEL": "gpt-6-astra",
        })
        item = next(x for x in manager.catalog() if x["name"] == "openai_responses")
        self.assertTrue(item["implemented"])
        self.assertTrue(item["ready"])
        self.assertEqual(item["configured_model"], "gpt-6-astra")
        self.assertNotIn("sensitive-test-key", repr(item))

    def test_catalog_does_not_mark_unconfigured_responses_ready(self):
        manager = ProviderManager(environ={})
        item = next(x for x in manager.catalog() if x["name"] == "openai_responses")
        self.assertFalse(item["ready"])
        self.assertEqual(item["status"], "api_key_not_configured")


if __name__ == "__main__":
    unittest.main()
