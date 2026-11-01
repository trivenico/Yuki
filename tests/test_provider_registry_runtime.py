import unittest
from providers.hybrid import HybridProvider
from providers.gemini_api import GeminiAPIProvider
from providers.mock import MockProvider
from providers.ollama import OllamaProvider
from yuki_providers import ProviderManager

class ProviderRegistryRuntimeTests(unittest.TestCase):
    def test_registry_reports_only_factory_supported_integrations(self):
        manager = ProviderManager(environ={})
        entries = {item["name"]: item for item in manager.list_providers()}
        self.assertNotIn("mock", entries)
        self.assertTrue(entries["ollama"]["implemented"])
        self.assertTrue(entries["hybrid"]["implemented"])
        self.assertTrue(entries["openai"]["implemented"])
        self.assertFalse(entries["anthropic"]["implemented"])
        self.assertTrue(entries["gemini"]["implemented"])
        self.assertTrue(entries["gemini-api"]["implemented"])

    def test_default_openai_adapter_is_constructible_without_credentials(self):
        from providers.openai_compatible import OpenAICompatibleProvider
        adapter = ProviderManager(environ={}).create_selected_adapter()
        self.assertIsInstance(adapter, OpenAICompatibleProvider)
        self.assertFalse(adapter.is_available())

    def test_gemini_api_is_selectable_and_uses_structured_adapter(self):
        manager = ProviderManager(environ={"GEMINI_API_KEY": "test-key"})
        self.assertEqual(manager.select("gemini-api"), "Selected provider: gemini-api")
        adapter = manager.create_selected_adapter()
        self.assertIsInstance(adapter, GeminiAPIProvider)
        self.assertTrue(adapter.is_available())

    def test_ollama_requires_configuration_and_failed_selection_is_atomic(self):
        manager = ProviderManager(environ={})
        message = manager.select("ollama")
        self.assertIn("Cannot select ollama", message)
        self.assertEqual(manager.current().name, "openai")

    def test_ollama_and_hybrid_use_shared_factory(self):
        manager = ProviderManager({"ollama_model": "test-model"}, environ={})
        self.assertEqual(manager.select("ollama"), "Selected provider: ollama")
        self.assertIsInstance(manager.create_selected_adapter(), OllamaProvider)
        self.assertEqual(manager.select("hybrid"), "Selected provider: hybrid")
        self.assertIsInstance(manager.create_selected_adapter(), HybridProvider)

    def test_unimplemented_provider_does_not_change_selection(self):
        manager = ProviderManager(environ={})
        message = manager.select("anthropic")
        self.assertIn("not implemented", message)
        self.assertEqual(manager.current().name, "openai")

    def test_custom_factory_must_return_provider_contract(self):
        manager = ProviderManager(environ={})
        manager.register(type(manager.current())("custom", (), True), factory=lambda: object())
        with self.assertRaisesRegex(TypeError, "generate"):
            manager._build_adapter("custom")

if __name__ == "__main__":
    unittest.main()
