import unittest
from unittest.mock import patch

from providers.catalog import build_provider_catalog


class ProviderCatalogTests(unittest.TestCase):
    def test_catalog_is_sanitized_and_does_not_leak_secrets(self):
        secret = "do-not-display-this-secret"
        catalog = build_provider_catalog(
            config={"provider": "hybrid", "ollama_model": "qwen3"},
            environ={
                "YUKI_OLLAMA_MODEL": "qwen3",
                "OPENAI_API_KEY": secret,
                "YUKI_CLOUD_MODEL": "model-x",
                "YUKI_CLOUD_API_KEY_ENV": "OPENAI_API_KEY",
            },
        )
        rendered = repr(catalog)
        self.assertNotIn(secret, rendered)
        hybrid = next(x for x in catalog if x["name"] == "hybrid")
        self.assertTrue(hybrid["credential_configured"])
        self.assertEqual(hybrid["configured_model"], "model-x")
        self.assertTrue(hybrid["selected"])
        self.assertFalse(any(x["name"] == "mock" for x in catalog))

    def test_openai_adapter_is_reported_ready_only_with_explicit_credentials(self):
        missing = next(x for x in build_provider_catalog(environ={}) if x["name"] == "openai")
        self.assertTrue(missing["implemented"])
        self.assertFalse(missing["ready"])
        configured = next(
            x for x in build_provider_catalog(
                environ={"OPENAI_API_KEY": "some-key", "YUKI_OPENAI_MODEL": "gpt-x"}
            ) if x["name"] == "openai"
        )
        self.assertTrue(configured["implemented"])
        self.assertTrue(configured["ready"])
        self.assertEqual(configured["status"], "credentials_and_model_configured")

    def test_ollama_uses_local_first_model_default(self):
        catalog = build_provider_catalog(config={"provider": "ollama"}, environ={})
        item = next(x for x in catalog if x["name"] == "ollama")
        self.assertTrue(item["ready"])
        self.assertEqual(item["configured_model"], "llama3.2:3b")
        self.assertTrue(item["selected"])

    @patch("providers.catalog.shutil_which", return_value="/usr/bin/gemini")
    def test_gemini_cli_readiness_does_not_claim_auth_verified(self, _):
        item = next(x for x in build_provider_catalog(environ={}) if x["name"] == "gemini")
        self.assertTrue(item["ready"])
        self.assertEqual(item["status"], "cli_detected_auth_not_verified")

    def test_additional_cloud_provider_readiness_requires_model_and_key(self):
        cases = (
            ("deepseek", "YUKI_DEEPSEEK_MODEL", "DEEPSEEK_API_KEY"),
            ("kimi", "YUKI_KIMI_MODEL", "MOONSHOT_API_KEY"),
            ("minimax", "YUKI_MINIMAX_MODEL", "MINIMAX_API_KEY"),
            ("openrouter", "YUKI_OPENROUTER_MODEL", "OPENROUTER_API_KEY"),
            ("tokenrouter", "YUKI_TOKENROUTER_MODEL", "TOKENROUTER_API_KEY"),
        )
        for name, model_env, key_env in cases:
            with self.subTest(provider=name):
                missing = next(item for item in build_provider_catalog(environ={}) if item["name"] == name)
                self.assertTrue(missing["implemented"])
                self.assertFalse(missing["ready"])
                configured = next(
                    item for item in build_provider_catalog(
                        config={"provider": name},
                        environ={model_env: "operator-model", key_env: "test-key"},
                    ) if item["name"] == name
                )
                self.assertTrue(configured["implemented"])
                self.assertTrue(configured["ready"])
                self.assertTrue(configured["credential_configured"])
                self.assertEqual(configured["configured_model"], "operator-model")
                self.assertEqual(configured["status"], "credentials_and_model_configured")

    def test_invalid_model_names_are_not_returned(self):
        item = next(
            x for x in build_provider_catalog(environ={"YUKI_OLLAMA_MODEL": "bad model"})
            if x["name"] == "ollama"
        )
        self.assertIsNone(item["configured_model"])
        self.assertFalse(item["ready"])


if __name__ == "__main__":
    unittest.main()
