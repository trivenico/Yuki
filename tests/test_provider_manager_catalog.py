import unittest
from yuki_providers import ProviderManager

class ProviderManagerCatalogTests(unittest.TestCase):
    def test_manager_exposes_catalog_without_secrets(self):
        secret = "top-secret-key"
        manager = ProviderManager(config={"provider":"ollama","ollama_model":"qwen3"},
            environ={"YUKI_OLLAMA_MODEL":"qwen3","OPENAI_API_KEY":secret})
        catalog = manager.catalog()
        self.assertTrue(any(item["name"] == "ollama" and item["selected"] for item in catalog))
        self.assertNotIn(secret, repr(catalog))

if __name__ == "__main__":
    unittest.main()
