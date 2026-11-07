import unittest

from providers.factory import create_provider
from providers.gemini_cli import GeminiCLIProvider
from providers.hybrid import HybridProvider
from providers.openai_compatible import OpenAICompatibleProvider
from providers.ollama import OllamaProvider


class ProviderFactoryTests(unittest.TestCase):
    def test_default_factory_uses_real_openai_provider(self):
        self.assertIsInstance(create_provider({}), OpenAICompatibleProvider)

    def test_local_provider_requires_model_configuration(self):
        with self.assertRaisesRegex(ValueError, "model"):
            create_provider({"provider": "ollama", "ollama_model": ""})


    def test_ollama_selection_is_local_only_even_when_cloud_credentials_exist(self):
        provider = create_provider(
            {
                "provider": "ollama",
                "ollama_model": "yuki-local",
                "ollama_base_url": "http://127.0.0.1:11434",
            },
            environ={
                "OPENAI_API_KEY": "must-not-be-used",
                "YUKI_CLOUD_MODEL": "cloud-model",
                "YUKI_CLOUD_TASKS": "general,private_task",
            },
        )
        self.assertIsInstance(provider, OllamaProvider)
        self.assertEqual(provider.model, "yuki-local")
        self.assertEqual(provider.base_url, "http://127.0.0.1:11434")
        self.assertFalse(hasattr(provider, "cloud_provider"))
        self.assertFalse(hasattr(provider, "cloud_tasks"))

    def test_ollama_selection_never_requires_cloud_credentials(self):
        provider = create_provider(
            {"provider": "ollama", "ollama_model": "yuki-local"},
            environ={},
        )
        self.assertIsInstance(provider, OllamaProvider)
        self.assertTrue(provider.is_available() is False)

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

    def test_mock_provider_selection_is_forbidden(self):
        with self.assertRaisesRegex(ValueError, "mock providers are forbidden"):
            create_provider({"provider": "mock"})

    def test_unknown_provider_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "provider must be one of"):
            create_provider({"provider": "automatic-cloud-fallback"})


if __name__ == "__main__":
    unittest.main()
