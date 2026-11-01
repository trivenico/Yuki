import unittest

from providers.anthropic_messages import AnthropicMessagesProvider
from yuki_providers import ProviderManager


class AnthropicRegistryTests(unittest.TestCase):
    def test_manager_selects_fable_provider(self):
        manager = ProviderManager(environ={
            "ANTHROPIC_API_KEY": "test-only-key",
            "YUKI_ANTHROPIC_MODEL": "claude-fable-5-1",
        })
        self.assertIn("anthropic", {item["name"] for item in manager.list_providers()})
        self.assertEqual(manager.select("anthropic"), "Selected provider: anthropic")
        self.assertIsInstance(manager.create_selected_adapter(), AnthropicMessagesProvider)

    def test_catalog_reports_readiness_without_exposing_key(self):
        secret = "sensitive-anthropic-test-key"
        manager = ProviderManager(environ={
            "ANTHROPIC_API_KEY": secret,
            "YUKI_ANTHROPIC_MODEL": "claude-fable-5-1",
        })
        item = next(x for x in manager.catalog() if x["name"] == "anthropic")
        self.assertTrue(item["implemented"])
        self.assertTrue(item["ready"])
        self.assertEqual(item["configured_model"], "claude-fable-5-1")
        self.assertNotIn(secret, repr(item))

    def test_unconfigured_anthropic_is_not_routable(self):
        manager = ProviderManager(environ={})
        item = next(x for x in manager.catalog() if x["name"] == "anthropic")
        self.assertTrue(item["implemented"])
        self.assertFalse(item["ready"])
        with self.assertRaises(LookupError):
            manager.routing_registry().route(allow_cloud=True, preference=("anthropic",))


if __name__ == "__main__":
    unittest.main()
