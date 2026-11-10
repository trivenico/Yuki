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


    def test_anthropic_catalog_reports_ready_without_exposing_api_key(self):
        secret = "anthropic-private-secret"
        manager = ProviderManager(
            config={"provider": "anthropic", "anthropic_model": "claude-test"},
            environ={"ANTHROPIC_API_KEY": secret, "YUKI_ANTHROPIC_MODEL": "claude-test"},
        )
        catalog = manager.catalog()
        item = next(value for value in catalog if value["name"] == "anthropic")
        self.assertTrue(item["implemented"])
        self.assertTrue(item["ready"])
        self.assertEqual(item["configured_model"], "claude-test")
        self.assertNotIn(secret, repr(catalog))

if __name__ == "__main__":
    unittest.main()
