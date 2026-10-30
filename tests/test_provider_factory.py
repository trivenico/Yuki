import unittest

from providers.factory import create_provider
from providers.gemini_cli import GeminiCLIProvider
from providers.hybrid import HybridProvider
from providers.mock import MockProvider
from providers.multi import MultiProviderRouter
from providers.openai_compatible import OpenAICompatibleProvider
from providers.ollama import OllamaProvider
from providers.oci_iam import OciIamProvider
from unittest.mock import Mock, patch
import json


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

    def test_oci_iam_provider_factory_uses_configured_principal_mode(self):
        with patch("providers.factory.create_oci_iam_auth", return_value=object()) as auth_factory:
            with patch("providers.factory.OciIamProvider") as provider_factory:
                create_provider(
                    {"provider": "oci_iam"},
                    environ={
                        "YUKI_OCI_REGION": "eu-frankfurt-1",
                        "YUKI_OCI_MODEL": "meta.llama",
                        "YUKI_OCI_PROJECT_OCID": "ocid1.generativeaiproject.oc1.eu-frankfurt-1.example",
                        "YUKI_OCI_AUTH_MODE": "instance_principal",
                    },
                )
        auth_factory.assert_called_once_with(
            "instance_principal", config_file=None, profile_name="DEFAULT"
        )
        provider_factory.assert_called_once()

    def test_oci_iam_provider_sends_project_and_parses_chat_completion(self):
        response = Mock()
        response.status_code = 200
        response.content = json.dumps({
            "choices": [{"message": {"content": "  OCI says hello  "}}]
        }).encode()
        response.raise_for_status.return_value = None
        client = Mock()
        client.post.return_value = response
        provider = OciIamProvider(
            "meta.llama",
            region="eu-frankfurt-1",
            project_ocid="ocid1.generativeaiproject.oc1.eu-frankfurt-1.example",
            auth=object(),
            http_client=client,
        )
        self.assertEqual(provider.generate("hello"), "OCI says hello")
        args, kwargs = client.post.call_args
        self.assertTrue(args[0].startswith(provider.base_url))
        self.assertEqual(kwargs["headers"]["openai-project"], provider.project_ocid)
        self.assertFalse(kwargs["json"]["stream"])

    def test_oci_iam_provider_rejects_redirects(self):
        response = Mock()
        response.status_code = 302
        client = Mock()
        client.post.return_value = response
        provider = OciIamProvider(
            "meta.llama",
            region="eu-frankfurt-1",
            project_ocid="ocid1.generativeaiproject.oc1.eu-frankfurt-1.example",
            auth=object(),
            http_client=client,
        )
        with self.assertRaisesRegex(RuntimeError, "redirect"):
            provider.generate("hello")

    def test_oci_iam_provider_requires_valid_project_and_region(self):
        with self.assertRaisesRegex(ValueError, "YUKI_OCI_REGION"):
            OciIamProvider(
                "meta.llama", region="", project_ocid="ocid1.generativeaiproject.x",
                auth=object(), http_client=Mock()
            )
        with self.assertRaisesRegex(ValueError, "YUKI_OCI_PROJECT_OCID"):
            OciIamProvider(
                "meta.llama", region="eu-frankfurt-1", project_ocid="bad",
                auth=object(), http_client=Mock()
            )

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

    def test_multi_provider_routes_managed_and_self_hosted_models(self):
        router = create_provider(
            {
                "provider": "multi",
                "provider_default_route": "oci_managed",
                "provider_routes": {
                    "reasoning": "oci_managed",
                    "coding": "self_hosted",
                },
            },
            environ={
                "YUKI_OCI_REGION": "eu-frankfurt-1",
                "YUKI_OCI_MODEL": "openai.gpt-oss-120b",
                "YUKI_OCI_PROJECT_OCID": "ocid1.generativeaiproject.oc1.eu-frankfurt-1.example",
                "OCI_GENAI_API_KEY": "test-oci-secret",
                "YUKI_SELF_HOSTED_MODEL": "qwen-coder",
                "YUKI_SELF_HOSTED_BASE_URL": "https://inference.yuki.example/v1",
                "YUKI_CLOUD_ALLOWED_HOSTS": "inference.yuki.example",
                "YUKI_SELF_HOSTED_API_KEY": "test-self-hosted-secret",
            },
        )
        self.assertIsInstance(router, MultiProviderRouter)
        self.assertEqual(router.provider_for("reasoning").model, "openai.gpt-oss-120b")
        self.assertEqual(router.provider_for("coding").model, "qwen-coder")
        self.assertTrue(router.provider_for("reasoning").is_available())
        self.assertTrue(router.provider_for("coding").is_available())

    def test_multi_provider_requires_explicit_trusted_self_hosted_endpoint(self):
        with self.assertRaisesRegex(ValueError, "YUKI_SELF_HOSTED_BASE_URL"):
            create_provider(
                {"provider": "multi", "provider_default_route": "oci_managed"},
                environ={
                    "YUKI_OCI_REGION": "eu-frankfurt-1",
                    "YUKI_OCI_MODEL": "openai.gpt-oss-120b",
                    "YUKI_OCI_PROJECT_OCID": "ocid1.generativeaiproject.oc1.eu-frankfurt-1.example",
                    "OCI_GENAI_API_KEY": "test-oci-secret",
                    "YUKI_SELF_HOSTED_MODEL": "qwen-coder",
                    "YUKI_SELF_HOSTED_BASE_URL": "https://inference.yuki.example/v1",
                },
            )

    def test_multi_provider_rejects_routes_to_unconfigured_providers(self):
        class AvailableProvider:
            def is_available(self):
                return True

            def generate(self, prompt):
                return prompt

        with self.assertRaisesRegex(ValueError, "unconfigured provider"):
            MultiProviderRouter(
                {"oci_managed": AvailableProvider()},
                routes={"coding": "self_hosted"},
                default_route="oci_managed",
            )

    def test_multi_provider_fails_closed_without_silent_fallback(self):
        class UnavailableProvider:
            def is_available(self):
                return False

            def generate(self, prompt):
                raise AssertionError("Unavailable provider must not be called.")

        router = MultiProviderRouter(
            {"oci_managed": UnavailableProvider(), "self_hosted": UnavailableProvider()},
            routes={"coding": "self_hosted"},
            default_route="oci_managed",
        )
        with self.assertRaisesRegex(RuntimeError, "fallback is disabled"):
            router.generate_for("coding", "implement a test")

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
