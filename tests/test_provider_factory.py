import unittest

from providers.factory import create_provider
from providers.gemini_cli import GeminiCLIProvider
from providers.hybrid import HybridProvider
from providers.openai_compatible import OpenAICompatibleProvider
from providers.ollama import OllamaProvider


class ProviderFactoryTests(unittest.TestCase):
    def test_default_factory_uses_local_ollama_provider(self):
        provider = create_provider({}, environ={})
        self.assertIsInstance(provider, OllamaProvider)
        self.assertEqual(provider.model, "llama3.2:3b")

    def test_mock_provider_is_rejected_by_runtime_factory(self):
        with self.assertRaisesRegex(ValueError, "test-only"):
            create_provider({"provider": "mock"}, environ={})

    def test_local_provider_requires_model_configuration(self):
        with self.assertRaisesRegex(ValueError, "model"):
            create_provider({"provider": "ollama", "ollama_model": ""})

    def test_gemini_provider_is_constructible_without_cli_installation(self):
        provider = create_provider({"provider": "gemini"}, environ={})
        self.assertIsInstance(provider, GeminiCLIProvider)
        self.assertFalse(provider.is_available())

    def test_hybrid_routes_to_local_without_explicit_cloud_tasks(self):
        provider = create_provider(
            {"provider": "hybrid", "ollama_model": "local-model"},
            environ={},
        )
        self.assertIsInstance(provider, HybridProvider)
        self.assertIsInstance(provider.local_provider, OllamaProvider)
        self.assertIsNone(provider.cloud_provider)
        self.assertEqual(provider.cloud_tasks, frozenset())

    def test_unknown_provider_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "provider must be one of"):
            create_provider({"provider": "automatic-cloud-fallback"})


if __name__ == "__main__":
    unittest.main()
