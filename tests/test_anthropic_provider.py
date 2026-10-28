import json
import unittest
from unittest.mock import MagicMock, patch

from providers.anthropic import AnthropicProvider
from providers.factory import create_provider
from providers.openai_compatible import OpenAICompatibleProvider
from providers.catalog import build_provider_catalog
from yuki_providers import ProviderManager


class AnthropicProviderTests(unittest.TestCase):
    def test_factory_constructs_native_anthropic_adapter(self):
        provider = create_provider(
            {"provider": "anthropic", "anthropic_model": "claude-test"},
            environ={"ANTHROPIC_API_KEY": "test-key"},
        )
        self.assertIsInstance(provider, AnthropicProvider)
        self.assertEqual(provider.model, "claude-test")
        self.assertTrue(provider.is_available())

    def test_anthropic_requires_explicit_model_and_key(self):
        with self.assertRaisesRegex(ValueError, "Configure a model"):
            create_provider({"provider": "anthropic"}, environ={})
        provider = AnthropicProvider("claude-test", environ={})
        self.assertFalse(provider.is_available())
        with self.assertRaisesRegex(RuntimeError, "ANTHROPIC_API_KEY"):
            provider.generate("hello")

    @patch("providers.anthropic.urllib.request.build_opener")
    def test_messages_request_uses_native_headers_and_parses_text(self, build_opener):
        response = MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = json.dumps({
            "content": [
                {"type": "text", "text": "Hello"},
                {"type": "tool_use", "id": "tool-1", "name": "not-executed", "input": {}},
                {"type": "text", "text": "from Anthropic"},
            ]
        }).encode()
        opener = MagicMock()
        opener.open.return_value = response
        build_opener.return_value = opener

        provider = AnthropicProvider(
            "claude-test",
            environ={"ANTHROPIC_API_KEY": "unit-test-secret"},
        )
        self.assertEqual(provider.generate("  say hello  "), "Hello\nfrom Anthropic")
        request = opener.open.call_args.args[0]
        self.assertTrue(request.full_url.endswith("/v1/messages"))
        self.assertEqual(request.get_header("X-api-key"), "unit-test-secret")
        self.assertEqual(request.get_header("Anthropic-version"), "2023-06-01")
        self.assertEqual(json.loads(request.data)["model"], "claude-test")
        self.assertEqual(json.loads(request.data)["messages"][0]["content"], "say hello")

    def test_endpoint_is_trusted_and_redirects_are_rejected(self):
        provider = AnthropicProvider("claude-test", environ={"ANTHROPIC_API_KEY": "test"})
        self.assertEqual(provider.base_url, "https://api.anthropic.com/v1")
        with self.assertRaisesRegex(ValueError, "not trusted"):
            AnthropicProvider(
                "claude-test",
                base_url="https://untrusted.example/v1",
                environ={"ANTHROPIC_API_KEY": "test"},
            )

    def test_catalog_and_manager_report_anthropic_as_implemented(self):
        entry = next(item for item in build_provider_catalog(
            config={"provider": "anthropic", "anthropic_model": "claude-test"},
            environ={"ANTHROPIC_API_KEY": "test"},
        ) if item["name"] == "anthropic")
        self.assertTrue(entry["implemented"])
        self.assertTrue(entry["ready"])
        self.assertEqual(entry["configured_model"], "claude-test")

        manager = ProviderManager(
            config={"provider": "ollama"},
            environ={"YUKI_ANTHROPIC_MODEL": "claude-test", "ANTHROPIC_API_KEY": "test"},
        )
        self.assertIn(
            {"name": "anthropic", "auth_methods": ["ANTHROPIC_API_KEY"],
             "implemented": True, "selected": False},
            manager.list_providers(),
        )
        self.assertEqual(manager.select("anthropic"), "Selected provider: anthropic")
        self.assertIsInstance(manager.create_selected_adapter(), AnthropicProvider)


if __name__ == "__main__":
    unittest.main()
