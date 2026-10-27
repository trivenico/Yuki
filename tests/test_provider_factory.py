import unittest

from providers.factory import create_provider
from providers.hybrid import HybridProvider
from providers.mock import MockProvider
from providers.ollama import OllamaProvider
from providers.openai_compatible import OpenAICompatibleProvider


class ProviderFactoryTests(unittest.TestCase):
    def test_unconfigured_factory_never_silently_selects_mock(self):
        with self.assertRaisesRegex(ValueError, "model"):
            create_provider({})

    def test_explicit_real_cloud_provider_is_constructible_without_credentials(self):
        provider = create_provider({"provider": "openai", "model": "gpt-4.1-mini"}, environ={})
        self.assertIsInstance(provider, OpenAICompatibleProvider)
        self.assertFalse(provider.is_available())

    def test_local_provider_requires_model_configuration(self):
        with self.assertRaisesRegex(ValueError, "model"):
            create_provider({"provider": "ollama", "ollama_model": ""})

    def test_ollama_factory_honors_onboarding_base_url(self):
        provider = create_provider({
            "provider": "ollama",
            "ollama_model": "local-model",
            "base_url": "http://127.0.0.1:11435",
        })
        self.assertIsInstance(provider, OllamaProvider)
        self.assertEqual(provider.base_url, "http://127.0.0.1:11435")

    def test_hybrid_routes_to_local_without_explicit_cloud_tasks(self):
        provider = create_provider(
            {"provider": "hybrid", "ollama_model": "local-model"},
            environ={},
        )
        self.assertIsInstance(provider, HybridProvider)
        self.assertIsInstance(provider.local_provider, OllamaProvider)
        self.assertIsNone(provider.cloud_provider)
        self.assertEqual(provider.cloud_tasks, frozenset())

    def test_hybrid_passes_cloud_approval_callback_to_underlying_provider(self):
        def approve(*_args, **_kwargs):
            return True

        provider = create_provider(
            {
                "provider": "hybrid",
                "local_provider": "ollama",
                "ollama_model": "local-model",
                "cloud_provider": "openai",
                "cloud_model": "cloud-model",
                "api_key_env": "OPENAI_API_KEY",
                "cloud_tasks": ["general"],
            },
            environ={},
            cloud_approver=approve,
        )
        self.assertIsNotNone(provider.cloud_provider)
        self.assertIs(provider.cloud_provider.approval_callback, approve)
        self.assertEqual(provider.cloud_tasks, frozenset({"general"}))

    def test_unknown_provider_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "provider must be one of"):
            create_provider({"provider": "automatic-cloud-fallback"})


if __name__ == "__main__":
    unittest.main()
