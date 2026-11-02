import unittest
from yuki_providers import ProviderManager

class ProviderManagerCatalogTests(unittest.TestCase):
    def test_explicit_cloud_selection_is_not_silently_used_without_consent(self):
        manager = ProviderManager(config={"provider":"openai"},
            environ={"OPENAI_API_KEY":"test-key","YUKI_OPENAI_MODEL":"gpt-test"})
        with self.assertRaises(LookupError):
            manager.create_routed_adapter(allow_cloud=False)

    def test_explicit_local_selection_is_used_as_strict_preference(self):
        manager = ProviderManager(config={"provider":"ollama","ollama_model":"qwen3"},
            environ={"YUKI_OLLAMA_MODEL":"qwen3"})
        with self.assertRaises(RuntimeError):
            # Selection reaches the Ollama adapter, whose local endpoint is not
            # configured in this test environment; it must not fall back to cloud.
            manager.create_routed_adapter(allow_cloud=False)

    def test_manager_exposes_catalog_without_secrets(self):
        secret = "top-secret-key"
        manager = ProviderManager(config={"provider":"ollama","ollama_model":"qwen3"},
            environ={"YUKI_OLLAMA_MODEL":"qwen3","OPENAI_API_KEY":secret})
        catalog = manager.catalog()
        self.assertTrue(any(item["name"] == "ollama" and item["selected"] for item in catalog))
        self.assertNotIn(secret, repr(catalog))

if __name__ == "__main__":
    unittest.main()
