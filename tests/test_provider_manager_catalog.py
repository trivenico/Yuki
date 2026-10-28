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

    def test_additional_provider_selection_uses_real_factory(self):
        cases = (
            ("deepseek", {"YUKI_DEEPSEEK_MODEL": "deepseek-chat", "DEEPSEEK_API_KEY": "test"}),
            ("kimi", {"YUKI_KIMI_MODEL": "kimi-k2", "MOONSHOT_API_KEY": "test"}),
            ("minimax", {"YUKI_MINIMAX_MODEL": "MiniMax-M2", "MINIMAX_API_KEY": "test"}),
            ("openrouter", {"YUKI_OPENROUTER_MODEL": "openai/gpt-4.1-mini", "OPENROUTER_API_KEY": "test"}),
            ("tokenrouter", {"YUKI_TOKENROUTER_MODEL": "vendor/model", "TOKENROUTER_API_KEY": "test"}),
        )
        for name, environ in cases:
            with self.subTest(provider=name):
                manager = ProviderManager(environ=environ)
                result = manager.select(name)
                self.assertEqual(result, f"Selected provider: {name}")
                adapter = manager.create_selected_adapter()
                self.assertEqual(adapter.model, environ[next(k for k in environ if k.startswith("YUKI_"))])
                self.assertTrue(adapter.is_available())
                self.assertEqual(manager.current().name, name)

if __name__ == "__main__":
    unittest.main()
