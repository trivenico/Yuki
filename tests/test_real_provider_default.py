"""Regression tests for real-provider runtime defaults."""
import unittest

from providers.openai_compatible import OpenAICompatibleProvider
from yuki_providers import ProviderManager


class RealProviderDefaultTests(unittest.TestCase):
    def test_runtime_registry_does_not_expose_mock_provider(self):
        manager = ProviderManager(environ={})
        names = {item["name"] for item in manager.list_providers()}
        self.assertNotIn("mock", names)
        self.assertIn("openai", names)

    def test_native_only_mode_is_selected_by_default_without_network_access(self):
        manager = ProviderManager(environ={})
        self.assertIsNone(manager.current())
        self.assertIsNone(manager.create_selected_adapter())

    def test_native_only_mode_is_supported_in_configuration(self):
        manager = ProviderManager(config={"provider": "none"}, environ={})
        self.assertIsNone(manager.current())
        self.assertIsNone(manager.create_selected_adapter())

    def test_provider_can_be_selected_through_explicit_environment_setting(self):
        manager = ProviderManager(environ={"YUKI_PROVIDER": "openai"})
        self.assertEqual(manager.current().name, "openai")
        self.assertIsInstance(manager.create_selected_adapter(), OpenAICompatibleProvider)

    def test_openai_is_available_only_when_explicitly_selected(self):
        manager = ProviderManager(environ={})
        self.assertIn("Selected provider: openai", manager.select("openai"))
        adapter = manager.create_selected_adapter()
        self.assertIsInstance(adapter, OpenAICompatibleProvider)
        self.assertFalse(adapter.is_available())

    def test_native_only_mode_can_be_reselected_after_a_provider(self):
        manager = ProviderManager(environ={})
        manager.select("openai")
        result = manager.select("none")
        self.assertIn("native-only mode", result)
        self.assertIsNone(manager.current())
        self.assertIsNone(manager.create_selected_adapter())

    def test_openai_key_is_read_from_environment(self):
        manager = ProviderManager(environ={
            "YUKI_PROVIDER": "openai",
            "OPENAI_API_KEY": "test-only-not-a-real-key",
        })
        adapter = manager.create_selected_adapter()
        self.assertIsInstance(adapter, OpenAICompatibleProvider)
        self.assertTrue(adapter.is_available())

    def test_mock_cannot_be_selected_from_runtime_registry(self):
        manager = ProviderManager(environ={})
        result = manager.select("mock")
        self.assertIn("Unknown provider", result)
        self.assertIsNone(manager.current())


if __name__ == "__main__":
    unittest.main()
