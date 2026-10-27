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
    def __init__(self, *, cloud_approver=None, cloud=False, fail=False):
        self.cloud_approver = cloud_approver
        self.cloud = cloud
        self.fail = fail
        self.prompts = []

    def is_available(self):
        return True

    def generate(self, prompt):
        self.prompts.append(prompt)
        if self.fail:
            raise RuntimeError("api_key=secret-value")
        if self.cloud and not self.cloud_approver("cloud", prompt):
            return "BLOCKED: no cloud approval"
        return "YUKI_REPLY"


class FakeProviderFactory:
    def __init__(self, *, cloud=False, fail=False):
        self.cloud = cloud
        self.fail = fail
        self.provider = None

    def __call__(self, config, *, cloud_approver=None):
        self.provider = FakeProvider(
            cloud_approver=cloud_approver, cloud=self.cloud, fail=self.fail
        )
        return self.provider


class DesktopBridgeTests(unittest.TestCase):
    def make_bridge(self, provider_name="ollama", *, cloud=False, fail=False):
        factory = FakeProviderFactory(cloud=cloud, fail=fail)
        bridge = DesktopBridge(
            FakeCoreAgent(),
            ai=type("AI", (), {"usage_tracker": None})(),
            config={"provider": provider_name},
            provider_factory=factory,
        )
        return bridge, factory

    def test_status_is_read_only_and_lists_capabilities(self):
        bridge, _ = self.make_bridge()
        status = bridge.get_status()
        self.assertTrue(status["ready"])
        self.assertEqual(status["skills"], ["time", "file_read"])
        self.assertFalse(status["cloud_confirmation_required"])
        self.assertIn("not executed", status["message"])

    def test_hybrid_general_cloud_route_requires_explicit_confirmation(self):
        factory = FakeProviderFactory(cloud=True)
        bridge = DesktopBridge(
            FakeCoreAgent(), ai=type("AI", (), {"usage_tracker": None})(),
            config={"provider": "hybrid", "cloud_tasks": ["general"]},
            provider_factory=factory,
        )
        self.assertTrue(bridge.get_status()["cloud_confirmation_required"])
        result = bridge.ask("Private prompt", cloud_approved=False)
        self.assertFalse(result["ok"])
        self.assertTrue(result["requires_confirmation"])
        self.assertEqual(factory.provider.prompts, [])

    def test_hybrid_non_general_cloud_routes_keep_general_chat_local(self):
        factory = FakeProviderFactory(cloud=False)
        bridge = DesktopBridge(
            FakeCoreAgent(), ai=type("AI", (), {"usage_tracker": None})(),
            config={"provider": "hybrid", "cloud_tasks": ["summarize_public_page"]},
            provider_factory=factory,
        )
        self.assertFalse(bridge.get_status()["cloud_confirmation_required"])
        self.assertTrue(bridge.ask("Hello")["ok"])

    def test_cloud_request_is_not_sent_without_explicit_confirmation(self):
        bridge, factory = self.make_bridge("openai", cloud=True)
        result = bridge.ask("Hello", cloud_approved=False)
        self.assertFalse(result["ok"])
        self.assertTrue(result["requires_confirmation"])
        self.assertEqual(factory.provider.prompts, [])

    def test_confirmed_cloud_request_requires_provider_approval_callback(self):
        bridge, factory = self.make_bridge("openai", cloud=True)
        result = bridge.ask("Hello", cloud_approved=True)
        self.assertTrue(result["ok"])
        self.assertEqual(result["message"], "YUKI_REPLY")
        self.assertEqual(factory.provider.prompts, ["Hello"])

    def test_cloud_approval_is_one_request_only(self):
        bridge, factory = self.make_bridge("openai", cloud=True)
        self.assertTrue(bridge.ask("First", cloud_approved=True)["ok"])
        second = bridge.ask("Second", cloud_approved=False)
        self.assertFalse(second["ok"])
        self.assertEqual(factory.provider.prompts, ["First"])

    def test_local_provider_does_not_require_cloud_confirmation(self):
        bridge, factory = self.make_bridge("ollama", cloud=False)
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
        with self.assertLogs("yuki", level="ERROR") as captured:
            result = bridge.ask("Hello")
        self.assertFalse(result["ok"])
        self.assertNotIn("secret-value", result["message"])
        self.assertNotIn("secret-value", "\\n".join(captured.output))
        self.assertEqual(factory.provider.prompts, ["Hello"])


    def test_model_catalog_is_available_to_desktop_settings(self):
        bridge, _ = self.make_bridge()
        options = bridge.get_model_options("cloud")
        provider_ids = {item["id"] for item in options["providers"]}
        self.assertIn("gemini_api", provider_ids)
        self.assertIn("nvidia", provider_ids)
        self.assertNotIn("ollama", provider_ids)

    def test_cloud_model_switch_requires_confirmation_before_persisting(self):
        bridge, _ = self.make_bridge()
        with patch("core.onboarding.save_config") as save:
            result = bridge.select_model({"provider": "gemini_api", "model": "gemini-2.5-flash"})
        self.assertFalse(result["ok"])
        self.assertTrue(result["requires_confirmation"])
        save.assert_not_called()

    def test_local_model_switch_persists_only_validated_nonsecret_settings(self):
        bridge, _ = self.make_bridge()
        class FakeAI:
            def __init__(self, provider, usage_tracker=None):
                self.provider = provider
                self.usage_tracker = usage_tracker
            def status(self): return "ready"
            def ask(self, prompt): return "YUKI_REPLY"
        bridge._agent_factory = FakeAI
        with patch("core.onboarding.save_config") as save:
            result = bridge.select_model({
                "provider": "ollama", "model": "qwen2.5:7b",
                "base_url": "http://127.0.0.1:11434",
            })
        self.assertTrue(result["ok"])
        self.assertEqual(result["provider"], "ollama")
        saved = save.call_args.args[0]
        self.assertEqual(saved["model"], "qwen2.5:7b")
        self.assertNotIn("api_key", saved)

if __name__ == "__main__":
    unittest.main()
