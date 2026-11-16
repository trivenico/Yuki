import unittest

from providers.factory import create_provider
from providers.gemini_cli import GeminiCLIProvider
from providers.gemini_api import GeminiAPIProvider
from providers.hybrid import HybridProvider
from providers.mock import MockProvider
from providers.openai_compatible import OpenAICompatibleProvider
from providers.ollama import OllamaProvider


class ProviderFactoryTests(unittest.TestCase):
    def test_default_factory_uses_real_openai_provider(self):
        self.assertIsInstance(create_provider({}), OpenAICompatibleProvider)

    def test_local_provider_requires_model_configuration(self):
        with self.assertRaisesRegex(ValueError, "model"):
            create_provider({"provider": "ollama", "ollama_model": ""})

    def test_gemini_provider_is_constructible_without_cli_installation(self):
        provider = create_provider({"provider": "gemini"}, environ={})
        self.assertIsInstance(provider, GeminiCLIProvider)
        self.assertFalse(provider.is_available())

    def test_gemini_api_provider_is_constructible_without_network_or_key(self):
        provider = create_provider({"provider": "gemini-api"}, environ={})
        self.assertIsInstance(provider, GeminiAPIProvider)
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

    def test_shared_timeout_is_applied_to_openai_provider(self):
        provider = create_provider({}, environ={"YUKI_PROVIDER_TIMEOUT_SECONDS": "12"})
        self.assertEqual(provider.timeout, 12.0)

    def test_shared_timeout_is_applied_to_local_ollama_provider(self):
        provider = create_provider(
            {"provider": "ollama", "ollama_model": "local-model"},
            environ={"YUKI_PROVIDER_TIMEOUT_SECONDS": "13"},
        )
        self.assertEqual(provider.timeout, 13.0)

    def test_shared_timeout_is_applied_to_both_hybrid_providers(self):
        provider = create_provider(
            {"provider": "hybrid", "ollama_model": "local-model"},
            environ={
                "YUKI_PROVIDER_TIMEOUT_SECONDS": "14",
                "YUKI_CLOUD_MODEL": "cloud-model",
                "OPENAI_API_KEY": "test-key",
            },
        )
        self.assertEqual(provider.local_provider.timeout, 14.0)
        self.assertEqual(provider.cloud_provider.timeout, 14.0)
    def test_unknown_provider_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "provider must be one of"):
            create_provider({"provider": "automatic-cloud-fallback"})


if __name__ == "__main__":
    unittest.main()
