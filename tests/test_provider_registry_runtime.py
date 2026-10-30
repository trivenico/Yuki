import unittest
from providers.hybrid import HybridProvider
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
        self.assertTrue(entries["anthropic"]["implemented"])
        self.assertTrue(entries["gemini"]["implemented"])

    def test_default_ollama_adapter_is_constructible_without_network_access(self):
        adapter = ProviderManager(environ={}).create_selected_adapter()
        self.assertIsInstance(adapter, OllamaProvider)
        self.assertEqual(adapter.model, "llama3.2:3b")

    def test_ollama_requires_configuration_and_failed_selection_is_atomic(self):
        manager = ProviderManager({"provider": "openai", "ollama_model": ""}, environ={})
        message = manager.select("ollama")
        self.assertIn("Cannot select ollama", message)
        self.assertEqual(manager.current().name, "openai")

    def test_ollama_and_hybrid_use_shared_factory(self):
        manager = ProviderManager({"ollama_model": "test-model"}, environ={})
        self.assertEqual(manager.select("ollama"), "Selected provider: ollama")
        self.assertIsInstance(manager.create_selected_adapter(), OllamaProvider)
        self.assertEqual(manager.select("hybrid"), "Selected provider: hybrid")
        self.assertIsInstance(manager.create_selected_adapter(), HybridProvider)

    def test_anthropic_selection_requires_configuration_and_keeps_current_provider(self):
        manager = ProviderManager(environ={})
        message = manager.select("anthropic")
        self.assertIn("Cannot select anthropic", message)
        self.assertEqual(manager.current().name, "ollama")
        configured = ProviderManager(
            {"anthropic_model": "claude-test"},
            environ={"ANTHROPIC_API_KEY": "test-key"},
        )
        self.assertEqual(configured.select("anthropic"), "Selected provider: anthropic")

    def test_custom_factory_must_return_provider_contract(self):
        manager = ProviderManager(environ={})
        manager.register(type(manager.current())("custom", (), True), factory=lambda: object())
        with self.assertRaisesRegex(TypeError, "generate"):
            manager._build_adapter("custom")

if __name__ == "__main__":
    unittest.main()
