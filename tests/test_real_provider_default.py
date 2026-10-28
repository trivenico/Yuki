"""Regression tests for real-provider runtime defaults."""
import unittest

from providers.ollama import OllamaProvider
from providers.openai_compatible import OpenAICompatibleProvider
from yuki_providers import ProviderManager


class RealProviderDefaultTests(unittest.TestCase):
    def test_runtime_registry_does_not_expose_mock_provider(self):
        manager = ProviderManager(environ={})
        names = {item["name"] for item in manager.list_providers()}
        self.assertNotIn("mock", names)
        self.assertIn("ollama", names)

    def test_ollama_is_selected_by_default_without_network_access(self):
        manager = ProviderManager(environ={})
        self.assertEqual(manager.current().name, "ollama")
        adapter = manager.create_selected_adapter()
        self.assertIsInstance(adapter, OllamaProvider)
        self.assertEqual(adapter.model, "llama3.2:3b")

    def test_openai_key_is_read_from_environment(self):
        manager = ProviderManager(config={"provider": "openai"}, environ={"OPENAI_API_KEY": "test-only-not-a-real-key"})
        adapter = manager.create_selected_adapter()
        self.assertIsInstance(adapter, OpenAICompatibleProvider)
        self.assertTrue(adapter.is_available())

    def test_mock_cannot_be_selected_from_runtime_registry(self):
        manager = ProviderManager(environ={})
        result = manager.select("mock")
        self.assertIn("Unknown provider", result)
        self.assertEqual(manager.current().name, "ollama")


if __name__ == "__main__":
    unittest.main()
