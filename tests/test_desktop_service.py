import json
import tempfile
import unittest
from pathlib import Path

from core.desktop_service import DesktopService, DesktopServiceError


class FakeAdapter:
    def __init__(self, available=True, response="YUKI_CONNECTION_OK"):
        self.available = available
        self.response = response
        self.prompts = []

    def is_available(self):
        return self.available

    def generate(self, prompt):
        self.prompts.append(prompt)
        return self.response


class FakeProviderManager:
    def __init__(self, adapter=None):
        self.adapter = adapter or FakeAdapter()
        self.selected = "openai"

    def list_providers(self):
        return [
            {"name": "openai", "implemented": True, "selected": self.selected == "openai"},
            {"name": "ollama", "implemented": True, "selected": self.selected == "ollama"},
            {"name": "anthropic", "implemented": False, "selected": False},
        ]

    def select(self, name):
        if name not in {"openai", "ollama"}:
            return "Provider is not implemented."
        self.selected = name
        return f"Selected provider: {name}"

    def create_selected_adapter(self):
        return self.adapter


class DesktopServiceTests(unittest.TestCase):
    def make_service(self, folder, manager=None):
        root = Path(folder)
        config = root / "config.yaml"
        config.write_text("name: Yuki\nworkspace: ./workspace\nprovider: openai\n", encoding="utf-8")
        env = {"YUKI_HOME": str(root / "data")}
        service = DesktopService(
            config_path=config,
            state_path=root / "data" / "onboarding.json",
            environ=env,
            provider_manager=manager or FakeProviderManager(),
        )
        service.mark_welcome_seen()
        return service

    def complete_setup(self, service):
        service.verify_provider("openai")
        service.acknowledge_privacy()
        service.acknowledge_permissions()

    def test_provider_verification_and_first_chat_complete_onboarding(self):
        with tempfile.TemporaryDirectory() as folder:
            manager = FakeProviderManager()
            service = self.make_service(folder, manager)
            result = service.verify_provider("openai")
            self.assertEqual(result["status"], "verified")
            service.acknowledge_privacy()
            service.acknowledge_permissions()
            answer = service.chat("Hello Yuki")
            self.assertEqual(answer, "YUKI_CONNECTION_OK")
            from core.desktop_onboarding import load_onboarding_state
            state = load_onboarding_state(service.state_path)
            self.assertTrue(state["completed"])
            self.assertEqual(state["completed_steps"][-1], "first_conversation")
            self.assertEqual(manager.adapter.prompts[-1], "Hello Yuki")

    def test_unimplemented_provider_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            service = self.make_service(folder)
            with self.assertRaises(DesktopServiceError):
                service.verify_provider("anthropic")

    def test_unavailable_provider_does_not_complete_provider_step(self):
        with tempfile.TemporaryDirectory() as folder:
            service = self.make_service(folder, FakeProviderManager(FakeAdapter(available=False)))
            with self.assertRaises(DesktopServiceError):
                service.verify_provider("openai")
            from core.desktop_onboarding import load_onboarding_state
            self.assertEqual(load_onboarding_state(service.state_path)["completed_steps"], ["welcome"])

    def test_chat_requires_privacy_and_permissions_acknowledgement(self):
        with tempfile.TemporaryDirectory() as folder:
            service = self.make_service(folder)
            service.verify_provider("openai")
            with self.assertRaises(DesktopServiceError):
                service.chat("Hello")
            service.acknowledge_privacy()
            with self.assertRaises(DesktopServiceError):
                service.chat("Hello")

    def test_chat_does_not_dispatch_skills_and_rejects_oversized_input(self):
        with tempfile.TemporaryDirectory() as folder:
            manager = FakeProviderManager()
            service = self.make_service(folder, manager)
            self.complete_setup(service)
            with self.assertRaises(DesktopServiceError):
                service.chat("x" * 8001)
            self.assertEqual(len(manager.adapter.prompts), 1)  # only the explicit provider probe

    def test_credentials_and_raw_provider_errors_are_not_persisted_or_exposed(self):
        with tempfile.TemporaryDirectory() as folder:
            manager = FakeProviderManager()
            service = self.make_service(folder, manager)
            service.verify_provider("openai")
            content = service.state_path.read_text(encoding="utf-8")
            self.assertNotIn("api_key", content.lower())
            self.assertNotIn("secret", content.lower())

            class BrokenAdapter(FakeAdapter):
                def generate(self, prompt):
                    raise RuntimeError("api_key=top-secret-value")
            manager.adapter = BrokenAdapter()
            service._verified_provider = "openai"
            service.acknowledge_privacy()
            service.acknowledge_permissions()
            with self.assertRaises(DesktopServiceError) as caught:
                service.chat("Hello")
            self.assertNotIn("top-secret-value", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
