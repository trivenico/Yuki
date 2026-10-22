import unittest

from providers.factory import create_provider
from providers.hybrid import HybridProvider
from providers.mock import MockProvider
from providers.ollama import OllamaProvider
from yuki_providers import ProviderManager


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

    def test_provider_timeout_is_passed_to_local_adapter(self):
        provider = create_provider(
            {"provider": "ollama", "ollama_model": "local-model"},
            environ={"YUKI_PROVIDER_TIMEOUT_SECONDS": "17"},
        )
        self.assertEqual(provider.timeout, 17.0)

    def test_provider_timeout_rejects_invalid_values(self):
        for value in ("abc", "0", "121"):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "YUKI_PROVIDER_TIMEOUT_SECONDS"):
                    create_provider(
                        {"provider": "ollama", "ollama_model": "local-model"},
                        environ={"YUKI_PROVIDER_TIMEOUT_SECONDS": value},
                    )

    def test_provider_timeout_is_passed_to_hybrid_cloud_adapter(self):
        import os
        from unittest.mock import patch
        env = {
            "YUKI_OLLAMA_MODEL": "local-model",
            "YUKI_CLOUD_MODEL": "cloud-model",
            "YUKI_CLOUD_TASKS": "summarize",
            "OPENAI_API_KEY": "test-only",
            "YUKI_PROVIDER_TIMEOUT_SECONDS": "22",
        }
        with patch.dict(os.environ, env, clear=True):
            provider = create_provider(
                {"provider": "hybrid", "ollama_model": "local-model"},
            )
        self.assertEqual(provider.local_provider.timeout, 22.0)
        self.assertEqual(provider.cloud_provider.timeout, 22.0)

    def test_subscription_cli_providers_are_reported_as_not_integrated(self):
        providers = {item["name"]: item for item in ProviderManager().list_providers()}
        for name in ("openai", "anthropic", "deepseek", "zai"):
            with self.subTest(name=name):
                self.assertIn(name, providers)
                self.assertFalse(providers[name]["implemented"])
        self.assertTrue(providers["gemini"]["implemented"])
        self.assertIn("official_gemini_cli_account_sign_in", providers["gemini"]["auth_methods"])

    def test_gemini_selection_requires_cli_installation(self):
        from unittest.mock import patch
        manager = ProviderManager()
        manager._selected = "mock"
        with patch("providers.gemini_cli.shutil.which", return_value=None):
            message = manager.select("gemini")
        self.assertIn("not installed", message)
        self.assertEqual(manager.current().name, "mock")

    def test_unknown_provider_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "provider must be one of"):
            create_provider({"provider": "automatic-cloud-fallback"})


if __name__ == "__main__":
    unittest.main()
