import unittest

from providers.factory import create_provider
from providers.gemini_cli import GeminiCLIProvider
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

    def test_hybrid_routes_to_local_without_explicit_cloud_tasks(self):
        provider = create_provider(
            {"provider": "hybrid", "ollama_model": "local-model"},
            environ={},
        )
        self.assertIsInstance(provider, HybridProvider)
        self.assertIsInstance(provider.local_provider, OllamaProvider)
        self.assertIsNone(provider.cloud_provider)
        self.assertEqual(provider.cloud_tasks, frozenset())

    def test_openai_factory_uses_configured_model_and_timeout(self):
        provider = create_provider(
            {"openai_model": "configured-model", "provider_timeout_seconds": 23},
            environ={},
        )
        self.assertEqual(provider.model, "configured-model")
        self.assertEqual(provider.timeout, 23)

    def test_openai_timeout_environment_override_is_validated(self):
        provider = create_provider({}, environ={"YUKI_PROVIDER_TIMEOUT_SECONDS": "12.5"})
        self.assertEqual(provider.timeout, 12.5)
        with self.assertRaisesRegex(ValueError, "YUKI_PROVIDER_TIMEOUT_SECONDS"):
            create_provider({}, environ={"YUKI_PROVIDER_TIMEOUT_SECONDS": "not-a-number"})
        with self.assertRaisesRegex(ValueError, "Timeout"):
            create_provider({}, environ={"YUKI_PROVIDER_TIMEOUT_SECONDS": "121"})

    def test_unknown_provider_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "provider must be one of"):
            create_provider({"provider": "automatic-cloud-fallback"})


if __name__ == "__main__":
    unittest.main()
