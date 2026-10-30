import unittest
from providers.hybrid import HybridProvider
from providers.mock import MockProvider
from providers.ollama import OllamaProvider
from yuki_providers import ProviderManager
from providers.factory import create_provider


def create_oci_iam_for_test(mode, env, auth):
    return create_provider({"provider": mode}, environ={**env, "YUKI_OCI_AUTH_MODE": "session"})

class ProviderRegistryRuntimeTests(unittest.TestCase):
    def test_registry_reports_only_factory_supported_integrations(self):
        manager = ProviderManager(environ={})
        entries = {item["name"]: item for item in manager.list_providers()}
        self.assertNotIn("mock", entries)
        self.assertTrue(entries["ollama"]["implemented"])
        self.assertTrue(entries["hybrid"]["implemented"])
        self.assertTrue(entries["openai"]["implemented"])
        self.assertFalse(entries["anthropic"]["implemented"])
        self.assertTrue(entries["gemini"]["implemented"])
        self.assertTrue(entries["oci"]["implemented"])
        self.assertTrue(entries["oci_iam"]["implemented"])
        self.assertTrue(entries["oci_responses"]["implemented"])
        self.assertTrue(entries["multi"]["implemented"])

    def test_default_openai_adapter_is_constructible_without_credentials(self):
        from providers.openai_compatible import OpenAICompatibleProvider
        adapter = ProviderManager(environ={}).create_selected_adapter()
        self.assertIsInstance(adapter, OpenAICompatibleProvider)
        self.assertFalse(adapter.is_available())

    def test_oci_iam_and_responses_adapters_are_constructible(self):
        from providers.oci_iam import OciIamProvider
        from providers.oci_responses import OciResponsesProvider
        from unittest.mock import Mock
        env = {"YUKI_OCI_REGION": "eu-frankfurt-1", "YUKI_OCI_MODEL": "model-x",
               "YUKI_OCI_PROJECT_OCID": "ocid1.generativeaiproject.oc1..example"}
        auth = Mock()
        manager = ProviderManager({"provider": "oci_iam"}, environ=env)
        manager._factories["oci_iam"] = lambda: create_oci_iam_for_test("oci_iam", env, auth)
        self.assertIsInstance(manager.create_selected_adapter(), OciIamProvider)
        manager = ProviderManager({"provider": "oci_responses"}, environ=env)
        manager._factories["oci_responses"] = lambda: create_oci_iam_for_test("oci_responses", env, auth)
        self.assertIsInstance(manager.create_selected_adapter(), OciResponsesProvider)

    def test_ollama_requires_configuration_and_failed_selection_is_atomic(self):
        manager = ProviderManager(environ={})
        message = manager.select("ollama")
        self.assertIn("Cannot select ollama", message)
        self.assertEqual(manager.current().name, "openai")

    def test_ollama_and_hybrid_use_shared_factory(self):
        manager = ProviderManager({"ollama_model": "test-model"}, environ={})
        self.assertEqual(manager.select("ollama"), "Selected provider: ollama")
        self.assertIsInstance(manager.create_selected_adapter(), OllamaProvider)
        self.assertEqual(manager.select("hybrid"), "Selected provider: hybrid")
        self.assertIsInstance(manager.create_selected_adapter(), HybridProvider)

    def test_unimplemented_provider_does_not_change_selection(self):
        manager = ProviderManager(environ={})
        message = manager.select("anthropic")
        self.assertIn("not implemented", message)
        self.assertEqual(manager.current().name, "openai")

    def test_oci_provider_selection_uses_shared_factory(self):
        env = {"YUKI_OCI_REGION": "eu-frankfurt-1", "YUKI_OCI_MODEL": "test-model",
               "YUKI_OCI_PROJECT_OCID": "ocid1.generativeaiproject.oc1..example",
               "OCI_GENAI_API_KEY": "test-secret"}
        from providers.openai_compatible import OpenAICompatibleProvider
        manager = ProviderManager({"provider": "oci"}, environ=env)
        adapter = manager.create_selected_adapter()
        self.assertIsInstance(adapter, OpenAICompatibleProvider)
        self.assertEqual(adapter.base_url, "https://inference.generativeai.eu-frankfurt-1.oci.oraclecloud.com/openai/v1")

    def test_custom_factory_must_return_provider_contract(self):
        manager = ProviderManager(environ={})
        manager.register(type(manager.current())("custom", (), True), factory=lambda: object())
        with self.assertRaisesRegex(TypeError, "generate"):
            manager._build_adapter("custom")

if __name__ == "__main__":
    unittest.main()
