import unittest
from unittest.mock import patch
from providers.catalog import build_provider_catalog

class ProviderCatalogTests(unittest.TestCase):
    def test_catalog_is_sanitized_and_does_not_leak_secrets(self):
        secret = "do-not-display-this-secret"
        catalog = build_provider_catalog(config={"provider":"hybrid","ollama_model":"qwen3"},
            environ={"YUKI_OLLAMA_MODEL":"qwen3","OPENAI_API_KEY":secret,
                     "YUKI_CLOUD_MODEL":"model-x","YUKI_CLOUD_API_KEY_ENV":"OPENAI_API_KEY"})
        rendered = repr(catalog)
        self.assertNotIn(secret, rendered)
        hybrid = next(x for x in catalog if x["name"] == "hybrid")
        self.assertTrue(hybrid["credential_configured"])
        self.assertEqual(hybrid["configured_model"], "model-x")
        self.assertTrue(hybrid["selected"])

    def test_openai_is_routable_only_when_key_and_model_are_configured(self):
        catalog = build_provider_catalog(environ={"OPENAI_API_KEY":"some-key","YUKI_OPENAI_MODEL":"gpt-x"})
        openai = next(x for x in catalog if x["name"] == "openai")
        self.assertTrue(openai["implemented"])
        self.assertTrue(openai["ready"])
        self.assertEqual(openai["configured_model"], "gpt-x")
        self.assertEqual(openai["status"], "api_key_and_model_configured")

        unconfigured = next(x for x in build_provider_catalog(environ={}) if x["name"] == "openai")
        self.assertTrue(unconfigured["implemented"])
        self.assertFalse(unconfigured["ready"])
        self.assertEqual(unconfigured["status"], "api_key_not_configured")

    def test_ollama_requires_model_configuration(self):
        catalog = build_provider_catalog(config={"provider":"ollama"}, environ={})
        item = next(x for x in catalog if x["name"] == "ollama")
        self.assertFalse(item["ready"])
        self.assertIsNone(item["configured_model"])

    @patch("providers.catalog.shutil_which", return_value="/usr/bin/gemini")
    def test_gemini_cli_readiness_does_not_claim_auth_verified(self, _):
        item = next(x for x in build_provider_catalog(environ={}) if x["name"] == "gemini")
        self.assertTrue(item["ready"])
        self.assertEqual(item["status"], "cli_detected_auth_not_verified")

    def test_invalid_model_names_are_not_returned(self):
        item = next(x for x in build_provider_catalog(environ={"YUKI_OLLAMA_MODEL":"bad model"}) if x["name"] == "ollama")
        self.assertIsNone(item["configured_model"])
        self.assertFalse(item["ready"])

if __name__ == "__main__":
    unittest.main()
