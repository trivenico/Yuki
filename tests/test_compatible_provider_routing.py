import unittest
from providers.factory import create_provider
from providers.openai_compatible import OpenAICompatibleProvider
from yuki_providers import ProviderManager

CASES = {
    "openai": ("YUKI_OPENAI_MODEL", "OPENAI_API_KEY", "https://api.openai.com/v1"),
    "deepseek": ("YUKI_DEEPSEEK_MODEL", "DEEPSEEK_API_KEY", "https://api.deepseek.com/v1"),
    "kimi": ("YUKI_KIMI_MODEL", "MOONSHOT_API_KEY", "https://api.moonshot.ai/v1"),
    "minimax": ("YUKI_MINIMAX_MODEL", "MINIMAX_API_KEY", "https://api.minimax.chat/v1"),
    "openrouter": ("YUKI_OPENROUTER_MODEL", "OPENROUTER_API_KEY", "https://openrouter.ai/api/v1"),
    "tokenrouter": ("YUKI_TOKENROUTER_MODEL", "TOKENROUTER_API_KEY", "https://api.tokenrouter.io/v1"),
    "huggingface": ("YUKI_HF_MODEL", "HF_TOKEN", "https://router.huggingface.co/v1"),
    "zai": ("YUKI_ZAI_MODEL", "ZAI_API_KEY", "https://api.z.ai/api/paas/v4"),
}

class CompatibleProviderRoutingTests(unittest.TestCase):
    def test_each_supported_adapter_uses_configured_model_and_endpoint(self):
        for name, (model_env, key_env, endpoint) in CASES.items():
            with self.subTest(provider=name):
                provider = create_provider({"provider": name}, environ={model_env: "org/model", key_env: "secret"})
                self.assertIsInstance(provider, OpenAICompatibleProvider)
                self.assertEqual(provider.model, "org/model")
                self.assertEqual(provider.base_url, endpoint)
                self.assertTrue(provider.is_available())

    def test_missing_model_fails_closed(self):
        for name in CASES:
            with self.subTest(provider=name):
                with self.assertRaisesRegex(ValueError, "exact model identifier"):
                    create_provider({"provider": name}, environ={})

    def test_missing_credential_does_not_report_available(self):
        provider = create_provider({"provider": "deepseek"}, environ={"YUKI_DEEPSEEK_MODEL": "deepseek-chat"})
        self.assertFalse(provider.is_available())

    def test_manager_can_select_implemented_compatible_provider(self):
        manager = ProviderManager(environ={"YUKI_DEEPSEEK_MODEL":"deepseek-chat","DEEPSEEK_API_KEY":"test"})
        self.assertEqual(manager.select("deepseek"), "Selected provider: deepseek")
        self.assertTrue(manager.current().implemented)
        self.assertIsInstance(manager.create_selected_adapter(), OpenAICompatibleProvider)

    def test_anthropic_remains_unimplemented_until_native_adapter_exists(self):
        manager = ProviderManager(environ={"ANTHROPIC_API_KEY":"test","YUKI_ANTHROPIC_MODEL":"claude-model"})
        self.assertIn("not implemented", manager.select("anthropic"))

if __name__ == "__main__":
    unittest.main()
