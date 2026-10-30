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

    def test_oci_provider_uses_regional_endpoint_and_project_header(self):
        provider = create_provider(
            {"provider": "oci"},
            environ={
                "YUKI_OCI_REGION": "us-chicago-1",
                "YUKI_OCI_MODEL": "meta.llama-4-maverick",
                "YUKI_OCI_PROJECT_OCID": "ocid1.generativeaiproject.oc1.us-chicago-1.example",
                "OCI_GENAI_API_KEY": "test-secret",
            },
        )
        self.assertIsInstance(provider, OpenAICompatibleProvider)
        self.assertEqual(
            provider.base_url,
            "https://inference.generativeai.us-chicago-1.oci.oraclecloud.com/openai/v1",
        )
        self.assertEqual(provider.api_key_env, "OCI_GENAI_API_KEY")
        self.assertEqual(
            provider.additional_headers["openai-project"],
            "ocid1.generativeaiproject.oc1.us-chicago-1.example",
        )
        self.assertTrue(provider.is_available())

    def test_oci_provider_requires_region_model_and_project(self):
        with self.assertRaisesRegex(ValueError, "YUKI_OCI_REGION"):
            create_provider({"provider": "oci"}, environ={})
        with self.assertRaisesRegex(ValueError, "YUKI_OCI_PROJECT_OCID"):
            create_provider(
                {"provider": "oci"},
                environ={
                    "YUKI_OCI_REGION": "us-chicago-1",
                    "YUKI_OCI_MODEL": "some-model",
                },
            )

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

    def test_unknown_provider_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "provider must be one of"):
            create_provider({"provider": "automatic-cloud-fallback"})


if __name__ == "__main__":
    unittest.main()
