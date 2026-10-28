import unittest
from unittest.mock import patch

from providers.factory import create_provider
from providers.gemini_cli import GeminiCLIProvider
from providers.hybrid import HybridProvider
from providers.ollama import OllamaProvider
from providers.openai_compatible import OpenAICompatibleProvider


class ProviderFactoryTests(unittest.TestCase):
    def test_default_factory_uses_local_ollama_provider(self):
        provider = create_provider({}, environ={})
        self.assertIsInstance(provider, OllamaProvider)
        self.assertEqual(provider.model, "llama3.2:3b")

    def test_environment_overrides_ollama_config(self):
        provider = create_provider(
            {"provider": "ollama", "ollama_model": "", "ollama_base_url": "http://127.0.0.1:11434"},
            environ={
                "YUKI_OLLAMA_MODEL": "custom-model:latest",
                "YUKI_OLLAMA_BASE_URL": "https://ollama.example.com",
            },
        )
        self.assertEqual(provider.model, "custom-model:latest")
        self.assertEqual(provider.base_url, "https://ollama.example.com")

    def test_blank_config_uses_default_model(self):
        provider = create_provider({"provider": "ollama", "ollama_model": ""}, environ={})
        self.assertEqual(provider.model, "llama3.2:3b")

    def test_explicit_openai_provider_is_constructible(self):
        self.assertIsInstance(
            create_provider({"provider": "openai"}, environ={}),
            OpenAICompatibleProvider,
        )

    def test_local_provider_requires_model_configuration(self):
        with self.assertRaisesRegex(ValueError, "model"):
            create_provider({"provider": "ollama", "ollama_model": ""}, environ={})

    def test_gemini_provider_is_constructible_without_cli_installation(self):
        with patch("providers.gemini_cli.shutil.which", return_value=None):
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

    def test_mock_provider_is_rejected_by_runtime_factory(self):
        with self.assertRaisesRegex(ValueError, "mock is test-only"):
            create_provider({"provider": "mock"}, environ={})

    def test_unknown_provider_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "provider must be one of"):
            create_provider({"provider": "automatic-cloud-fallback"}, environ={})


if __name__ == "__main__":
    unittest.main()
