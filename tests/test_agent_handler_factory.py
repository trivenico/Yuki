"""Tests for constructing specialist handlers from persisted agent policy."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from automation.agent_handler_factory import build_agent_handlers
from automation.sqlite_agent_registry import SQLiteAgentRegistry
from core.agent_contracts import AgentSpec


class FakeProvider:
    def is_available(self):
        return True

    def generate(self, prompt):
        return "specialist response"


class AgentHandlerFactoryTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.registry = SQLiteAgentRegistry(str(Path(self.temp_dir.name) / "yuki.db"))

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_uses_each_agent_provider_preferences_and_cloud_consent(self):
        self.registry.register(AgentSpec(
            "local-researcher", "research", "Local-only research",
            provider_preferences=("ollama",), allow_cloud=False,
        ))
        self.registry.register(AgentSpec(
            "cloud-tester", "testing", "Cloud-approved test design",
            provider_preferences=("openai",), allow_cloud=True,
        ))
        provider = FakeProvider()
        manager = Mock()
        manager.create_routed_adapter.return_value = provider
        manager.create_selected_adapter.return_value = provider
        handlers = build_agent_handlers(manager, self.registry)

        self.assertEqual(set(handlers), {"local-researcher", "cloud-tester"})
        self.assertEqual(handlers["local-researcher"]("inspect"), "specialist response")
        calls = manager.create_routed_adapter.call_args_list
        self.assertEqual(len(calls), 2)
        routed = {(tuple(call.kwargs["preference"]), call.kwargs["allow_cloud"]) for call in calls}
        self.assertEqual(routed, {(("ollama",), False), (("openai",), True)})

    def test_uses_selected_provider_for_agent_without_preferences(self):
        self.registry.register(AgentSpec("reporter", "reporting", "Creates reports"))
        provider = FakeProvider()
        manager = Mock()
        manager.create_selected_adapter.return_value = provider
        manager.current.return_value = None

        handlers = build_agent_handlers(manager, self.registry)

        self.assertIn("reporter", handlers)
        manager.create_selected_adapter.assert_called_once_with()

    def test_cloud_opt_in_without_preference_uses_only_user_selected_provider(self):
        self.registry.register(AgentSpec(
            "cloud-reporter", "reporting", "Cloud-approved reporting", allow_cloud=True,
        ))
        provider = FakeProvider()
        manager = Mock()
        manager.current.return_value.name = "openai"
        manager.create_routed_adapter.return_value = provider

        handlers = build_agent_handlers(manager, self.registry)

        self.assertIn("cloud-reporter", handlers)
        manager.create_routed_adapter.assert_called_once_with(
            required=("text",), preference=("openai",), allow_cloud=True,
        )

    def test_cloud_opt_in_fails_closed_without_selected_provider(self):
        self.registry.register(AgentSpec(
            "cloud-reporter", "reporting", "Cloud-approved reporting", allow_cloud=True,
        ))
        manager = Mock()
        manager.current.return_value = None
        with self.assertRaises(RuntimeError):
            build_agent_handlers(manager, self.registry)
        manager.create_selected_adapter.assert_not_called()

    def test_rejects_registry_with_unsupported_role(self):
        self.registry.register(AgentSpec("unknown", "unknown-role", "Unknown role"))
        manager = Mock()
        with self.assertRaises(ValueError):
            build_agent_handlers(manager, self.registry)


if __name__ == "__main__":
    unittest.main()
