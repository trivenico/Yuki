import unittest
from unittest.mock import patch
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
        self.assertFalse(entries["anthropic"]["implemented"])
        self.assertTrue(entries["gemini"]["implemented"])

    def test_default_openai_adapter_is_constructible_without_credentials(self):
        from providers.openai_compatible import OpenAICompatibleProvider
        adapter = ProviderManager(environ={}).create_selected_adapter()
        self.assertIsInstance(adapter, OpenAICompatibleProvider)
        self.assertFalse(adapter.is_available())

    def test_ollama_requires_configuration_and_failed_selection_is_atomic(self):
        manager = ProviderManager(environ={})
        message = manager.select("ollama")
        self.assertIn("Cannot select ollama", message)
        self.assertEqual(manager.current().name, "openai")

    def test_ollama_and_hybrid_use_shared_factory(self):
        manager = ProviderManager({"ollama_model": "test-model"}, environ={})
        # Selection now validates runtime availability before committing. Keep
        # this unit test independent of a developer's local Ollama daemon.
        with patch.object(OllamaProvider, "is_available", return_value=True):
            self.assertEqual(manager.select("ollama"), "Selected provider: ollama")
            self.assertIsInstance(manager.create_selected_adapter(), OllamaProvider)
            self.assertEqual(manager.select("hybrid", allow_cloud=True), "Selected provider: hybrid")
            self.assertIsInstance(manager.create_selected_adapter(), HybridProvider)

    def test_selected_openai_provider_exposes_bounded_agent_model(self):
        from providers.agent_adapter import ProviderAgentAdapter
        manager = ProviderManager(environ={"OPENAI_API_KEY": "test-key"})
        model = manager.create_selected_agent_model()
        self.assertIsInstance(model, ProviderAgentAdapter)

    def test_selected_ollama_provider_exposes_bounded_agent_model(self):
        from providers.agent_adapter import ProviderAgentAdapter
        manager = ProviderManager(
            {"provider": "ollama", "ollama_model": "test-model"}, environ={}
        )
        model = manager.create_selected_agent_model()
        self.assertIsInstance(model, ProviderAgentAdapter)

    def test_text_only_hybrid_provider_fails_closed_for_agent_chat(self):
        manager = ProviderManager(
            {"provider": "hybrid", "ollama_model": "test-model"}, environ={}
        )
        with self.assertRaisesRegex(RuntimeError, "does not support structured agent chat"):
            manager.create_selected_agent_model()

    def test_cloud_provider_selection_requires_explicit_consent(self):
        from providers.gemini_cli import GeminiCLIProvider
        manager = ProviderManager(environ={})
        with patch.object(GeminiCLIProvider, "is_available", return_value=True):
            rejected = manager.select("gemini")
            self.assertIn("explicit cloud consent", rejected)
            self.assertEqual(manager.current().name, "openai")
            accepted = manager.select("gemini", allow_cloud=True)
            self.assertEqual(accepted, "Selected provider: gemini")
            self.assertIsInstance(manager.create_selected_adapter(), GeminiCLIProvider)
        manager = ProviderManager({"ollama_model": "test-model"}, environ={})
        with patch.object(OllamaProvider, "is_available", return_value=True):
            rejected = manager.select("hybrid")
            self.assertIn("explicit cloud consent", rejected)
            self.assertEqual(manager.current().name, "openai")
            accepted = manager.select("hybrid", allow_cloud=True)
            self.assertEqual(accepted, "Selected provider: hybrid")

    def test_unavailable_non_exact_provider_does_not_commit_selection(self):
        manager = ProviderManager(environ={})

        class UnavailableProvider:
            def generate(self, prompt):
                return prompt

            def is_available(self):
                return False

        manager.register(type(manager.current())("custom", (), True), factory=UnavailableProvider)
        message = manager.select("custom")
        self.assertIn("Cannot select custom", message)
        self.assertEqual(manager.current().name, "openai")
        self.assertIsNone(manager._selected_adapter)

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
