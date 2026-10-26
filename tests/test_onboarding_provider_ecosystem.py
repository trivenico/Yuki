import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.onboarding import load_user_config, onboarding_needed, save_config, validate_config
from providers.factory import create_provider
from providers.openai_compatible import OpenAICompatibleProvider
from providers.anthropic import AnthropicProvider
from ui.onboarding_tui import provider_choices


class OnboardingConfigTests(unittest.TestCase):
    def test_onboarding_choices_prefer_real_local_provider_and_exclude_mock(self):
        choices = provider_choices()
        self.assertEqual(choices[0], "ollama")
        self.assertNotIn("mock", choices)

    def test_atomic_config_round_trip_and_private_mode(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            self.assertTrue(onboarding_needed(path))
            save_config({"provider":"openai","model":"model-x","api_key":"must-not-persist",
                         "guardrails":{"require_tool_approval":True}}, path)
            loaded = load_user_config(path)
            self.assertEqual(loaded["provider"], "openai")
            self.assertNotIn("api_key", json.loads(path.read_text()))
            self.assertFalse(onboarding_needed(path))
            if os.name == "posix":
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_rejects_http_mcp_endpoint(self):
        with self.assertRaises(ValueError):
            validate_config({"mcp_servers":[{"name":"bad","endpoint":"http://example.com","allowed_tools":[]} ]})

    def test_mock_provider_is_not_available_in_production_onboarding(self):
        with self.assertRaises(ValueError):
            validate_config({"provider": "mock"})

    def test_guardrails_default_to_approval_and_local_first(self):
        result = validate_config({"provider":"ollama"})
        self.assertTrue(result["guardrails"]["require_tool_approval"])
        self.assertTrue(result["guardrails"]["require_cloud_approval"])
        self.assertTrue(result["guardrails"]["local_first"])


class ProviderEcosystemTests(unittest.TestCase):
    def test_openai_compatible_custom_endpoint_requires_https_or_loopback(self):
        with self.assertRaises(ValueError):
            OpenAICompatibleProvider("m", base_url="http://example.com/v1", allow_http_loopback=True)
        local = OpenAICompatibleProvider("m", api_key_env="YUKI_LOCAL_DUMMY_KEY", base_url="http://127.0.0.1:1234/v1", allow_http_loopback=True)
        self.assertTrue(local.is_available())

    def test_external_cloud_adapter_fails_closed_without_approval_callback(self):
        provider = OpenAICompatibleProvider("model", api_key_env="OPENAI_API_KEY",
                                            base_url="https://api.openai.com/v1",
                                            environ={"OPENAI_API_KEY":"test"})
        with self.assertRaisesRegex(RuntimeError, "not explicitly approved"):
            provider.generate("sensitive prompt")

    def test_provider_factory_supports_anthropic_adapter(self):
        provider = create_provider({"provider":"anthropic","model":"claude-model"}, environ={"ANTHROPIC_API_KEY":"test"})
        self.assertIsInstance(provider, AnthropicProvider)

    def test_provider_factory_supports_local_compatible_server(self):
        provider = create_provider({"provider":"lm-studio","model":"local-model"}, environ={})
        self.assertIsInstance(provider, OpenAICompatibleProvider)
        self.assertTrue(provider.is_available())


if __name__ == "__main__":
    unittest.main()
