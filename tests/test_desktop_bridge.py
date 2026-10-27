"""Desktop bridge tests against the current shared model-selection runtime."""
import unittest
from unittest.mock import patch

from ui.desktop import DesktopBridge


class FakeRegistry:
    def names(self):
        return ["time", "file_read"]


class FakeCoreAgent:
    registry = FakeRegistry()

    def available_skills(self):
        return [{"name": "time"}, {"name": "file_read"}]


class FakeProvider:
    def __init__(self, *, fail=False):
        self.fail = fail
        self.prompts = []

    def is_available(self):
        return True

    def generate(self, prompt):
        self.prompts.append(prompt)
        if self.fail:
            raise RuntimeError("api_key=secret-value")
        return "YUKI_REPLY"


class FakeProviderFactory:
    def __init__(self, *, fail=False):
        self.fail = fail
        self.provider = None
        self.configs = []
        self.raise_on_call = False

    def __call__(self, config):
        self.configs.append(dict(config))
        if self.raise_on_call:
            raise RuntimeError("provider configuration invalid")
        self.provider = FakeProvider(fail=self.fail)
        return self.provider


class FakeAI:
    def __init__(self, provider, usage_tracker=None):
        self.provider = provider
        self.usage_tracker = usage_tracker

    def status(self):
        return "ready"

    def ask(self, prompt):
        return self.provider.generate(prompt)


class DesktopBridgeTests(unittest.TestCase):
    def make_bridge(self, provider_name="ollama", *, fail=False):
        factory = FakeProviderFactory(fail=fail)
        bridge = DesktopBridge(
            FakeCoreAgent(),
            ai=type("AI", (), {"usage_tracker": None})(),
            config={"provider": provider_name, "ollama_model": "llama3.2:3b"},
            provider_factory=factory,
            agent_factory=FakeAI,
        )
        return bridge, factory

    def test_status_lists_capabilities_without_exposing_skill_execution(self):
        bridge, _ = self.make_bridge()
        status = bridge.get_status()
        self.assertTrue(status["ready"])
        self.assertEqual(status["skills"], ["time", "file_read"])
        self.assertFalse(status["cloud_confirmation_required"])
        self.assertIn("not executed", status["message"])

    def test_cloud_request_is_not_sent_without_confirmation(self):
        bridge, factory = self.make_bridge("openai")
        result = bridge.ask("Private prompt", cloud_approved=False)
        self.assertFalse(result["ok"])
        self.assertTrue(result["requires_confirmation"])
        self.assertEqual(factory.provider.prompts, [])

    def test_gemini_cli_provider_is_treated_as_cloud(self):
        bridge, factory = self.make_bridge("gemini")
        result = bridge.ask("Private prompt", cloud_approved=False)
        self.assertFalse(result["ok"])
        self.assertTrue(result["requires_confirmation"])
        self.assertEqual(factory.provider.prompts, [])

    def test_confirmed_cloud_request_is_sent_once(self):
        bridge, factory = self.make_bridge("openai")
        result = bridge.ask("Hello", cloud_approved=True)
        self.assertTrue(result["ok"])
        self.assertEqual(result["message"], "YUKI_REPLY")
        self.assertEqual(factory.provider.prompts, ["Hello"])
        second = bridge.ask("Second", cloud_approved=False)
        self.assertFalse(second["ok"])
        self.assertEqual(factory.provider.prompts, ["Hello"])

    def test_local_provider_does_not_require_cloud_confirmation(self):
        bridge, factory = self.make_bridge()
        result = bridge.ask("Hello")
        self.assertTrue(result["ok"])
        self.assertEqual(factory.provider.prompts, ["Hello"])

    def test_prompt_limit_is_enforced_before_provider_call(self):
        bridge, factory = self.make_bridge()
        result = bridge.ask("x" * 8001)
        self.assertFalse(result["ok"])
        self.assertEqual(factory.provider.prompts, [])

    def test_provider_error_details_are_not_returned_to_ui(self):
        bridge, factory = self.make_bridge(fail=True)
        result = bridge.ask("Hello")
        self.assertFalse(result["ok"])
        self.assertNotIn("secret-value", result["message"])
        self.assertEqual(factory.provider.prompts, ["Hello"])

    def test_desktop_catalog_uses_shared_provider_options(self):
        bridge, _ = self.make_bridge()
        options = bridge.get_model_options("cloud")
        provider_ids = {item["id"] for item in options["providers"]}
        self.assertIn("gemini_api", provider_ids)
        self.assertIn("nvidia", provider_ids)
        self.assertNotIn("ollama", provider_ids)

    def test_cloud_model_switch_requires_confirmation_before_persisting(self):
        bridge, factory = self.make_bridge()
        with patch("core.model_selection.persist_model_selection") as persist:
            result = bridge.select_model({"provider": "openai", "model": "gpt-4.1-mini"})
        self.assertFalse(result["ok"])
        self.assertTrue(result["requires_confirmation"])
        persist.assert_not_called()
        self.assertEqual(len(factory.configs), 1)

    def test_unsupported_catalog_provider_fails_closed(self):
        bridge, factory = self.make_bridge()
        with patch("core.model_selection.persist_model_selection") as persist:
            result = bridge.select_model({
                "provider": "gemini_api",
                "model": "gemini-2.5-flash",
                "base_url": "https://generativelanguage.googleapis.com/v1beta",
            }, cloud_approved=True)
        self.assertFalse(result["ok"])
        self.assertIn("not yet supported", result["message"])
        persist.assert_not_called()
        self.assertEqual(len(factory.configs), 1)

    def test_local_model_switch_persists_validated_settings_and_activates_provider(self):
        bridge, factory = self.make_bridge()
        with patch("core.model_selection.persist_model_selection") as persist:
            result = bridge.select_model({
                "provider": "ollama",
                "model": "qwen2.5:7b",
                "base_url": "http://127.0.0.1:11434",
            })
        self.assertTrue(result["ok"])
        self.assertEqual(result["provider"], "ollama")
        persist.assert_called_once()
        self.assertEqual(persist.call_args.args[0]["model"], "qwen2.5:7b")
        self.assertNotIn("api_key", persist.call_args.args[0])
        self.assertEqual(factory.configs[-1]["ollama_model"], "qwen2.5:7b")

    def test_provider_factory_failure_does_not_persist_or_activate(self):
        bridge, factory = self.make_bridge()
        factory.raise_on_call = True
        with patch("core.model_selection.persist_model_selection") as persist:
            result = bridge.select_model({
                "provider": "ollama",
                "model": "qwen2.5:7b",
                "base_url": "http://127.0.0.1:11434",
            })
        self.assertFalse(result["ok"])
        persist.assert_not_called()
        self.assertEqual(bridge.config["ollama_model"], "llama3.2:3b")

    def test_cloud_model_switch_persists_only_after_confirmation(self):
        bridge, factory = self.make_bridge()
        with patch("core.model_selection.persist_model_selection") as persist:
            result = bridge.select_model({
                "provider": "openai",
                "model": "gpt-4.1-mini",
                "base_url": "https://api.openai.com/v1",
            }, cloud_approved=True)
        self.assertTrue(result["ok"])
        persist.assert_called_once()
        self.assertEqual(factory.configs[-1]["openai_model"], "gpt-4.1-mini")


if __name__ == "__main__":
    unittest.main()
