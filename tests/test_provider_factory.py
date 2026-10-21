import unittest

from providers.factory import create_provider
from providers.hybrid import HybridProvider
from providers.mock import MockProvider
from providers.ollama import OllamaProvider


class ProviderFactoryTests(unittest.TestCase):
    def test_default_factory_keeps_mock_provider(self):
        self.assertIsInstance(create_provider({}), MockProvider)

    def test_local_provider_requires_model_configuration(self):
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

    def test_production_factory_rejects_mock_even_without_config_loader(self):
        with self.assertRaisesRegex(RuntimeError, "Production requires a real provider"):
            create_provider({"environment": "production", "provider": "mock"}, environ={})

    def test_factory_rejects_invalid_environment(self):
        with self.assertRaisesRegex(ValueError, "environment must be"):
            create_provider({"environment": "prod", "provider": "ollama", "ollama_model": "m"}, environ={})

    def test_staging_factory_requires_model(self):
        with self.assertRaisesRegex(RuntimeError, "model is required"):
            create_provider({"environment": "staging", "provider": "ollama"}, environ={})

    def test_provider_environment_override_is_validated(self):
        with self.assertRaisesRegex(RuntimeError, "Production requires a real provider"):
            create_provider({"environment": "production", "provider": "ollama", "ollama_model": "model"},
                            environ={"YUKI_PROVIDER": "mock", "YUKI_OLLAMA_MODEL": "model"})

    def test_unknown_provider_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "provider must be one of"):
            create_provider({"provider": "automatic-cloud-fallback"})


if __name__ == "__main__":
    unittest.main()
