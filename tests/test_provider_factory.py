import unittest

from providers.factory import create_provider
from providers.hybrid import HybridProvider
from providers.mock import MockProvider
from providers.ollama import OllamaProvider


class ProviderFactoryTests(unittest.TestCase):
    def test_default_factory_keeps_mock_provider(self):
        self.assertIsInstance(create_provider({}), MockProvider)

    def test_auto_provider_detects_installed_local_model(self):
        from unittest.mock import patch

        with patch("providers.factory.OllamaProvider.is_available", return_value=True):
            provider = create_provider({"provider": "auto"}, environ={})
        self.assertIsInstance(provider, OllamaProvider)

    def test_auto_provider_falls_back_to_mock_when_ollama_unavailable(self):
        from unittest.mock import patch

        with patch("providers.factory.OllamaProvider.is_available", return_value=False):
            provider = create_provider({"provider": "auto"}, environ={})
        self.assertIsInstance(provider, MockProvider)


    def test_explicit_local_provider_requires_model_configuration(self):
        with self.assertRaisesRegex(ValueError, "model"):
            create_provider({"provider": "ollama", "ollama_model": ""})

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
