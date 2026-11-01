import unittest

from providers.factory import create_provider
from providers.gemini_cli import GeminiCLIProvider
from providers.hybrid import HybridProvider
from providers.mock import MockProvider
from providers.openai_compatible import OpenAICompatibleProvider
from providers.openai_responses import OpenAIResponsesProvider
from providers.anthropic_messages import AnthropicMessagesProvider
from providers.ollama import OllamaProvider


class ProviderFactoryTests(unittest.TestCase):
    def test_default_factory_uses_real_openai_provider(self):
        self.assertIsInstance(create_provider({}), OpenAICompatibleProvider)

    def test_openai_responses_requires_explicit_available_model_id(self):
        with self.assertRaisesRegex(ValueError, "YUKI_OPENAI_RESPONSES_MODEL"):
            create_provider({"provider": "openai_responses"}, environ={"OPENAI_API_KEY": "test"})
        adapter = create_provider({"provider": "openai_responses"}, environ={
            "OPENAI_API_KEY": "test-only-key", "YUKI_OPENAI_RESPONSES_MODEL": "gpt-6-astra"
        })
        self.assertIsInstance(adapter, OpenAIResponsesProvider)
        self.assertEqual(adapter.model, "gpt-6-astra")

    def test_anthropic_requires_explicit_model_and_constructs_provider(self):
        with self.assertRaisesRegex(ValueError, "YUKI_ANTHROPIC_MODEL"):
            create_provider({"provider": "anthropic"}, environ={"ANTHROPIC_API_KEY": "test"})
        adapter = create_provider({"provider": "anthropic"}, environ={
            "ANTHROPIC_API_KEY": "test-only-key", "YUKI_ANTHROPIC_MODEL": "claude-fable-5-1"
        })
        self.assertIsInstance(adapter, AnthropicMessagesProvider)
        self.assertEqual(adapter.model, "claude-fable-5-1")

    def test_local_provider_requires_model_configuration(self):
        with self.assertRaisesRegex(ValueError, "model"):
            create_provider({"provider": "ollama", "ollama_model": ""})

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
