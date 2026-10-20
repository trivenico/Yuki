import unittest

from providers.factory import create_provider
from providers.hybrid import HybridProvider
from providers.mock import MockProvider
from providers.ollama import OllamaProvider


class ProviderFactoryTests(unittest.TestCase):
    def test_default_factory_keeps_mock_provider(self):
        self.assertIsInstance(create_provider({}), MockProvider)

    def test_local_provider_enables_safe_model_auto_selection_when_unconfigured(self):
        provider = create_provider({"provider": "ollama", "ollama_model": ""}, environ={})
        self.assertIsInstance(provider, OllamaProvider)
        self.assertTrue(provider.auto_select)
        self.assertEqual(provider.model, "")

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
