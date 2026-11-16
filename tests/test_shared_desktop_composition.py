"""Tests for desktop composition across durable chat and specialist APIs."""
import unittest
from unittest.mock import patch

from ui.desktop.shared_desktop_composition import build_shared_desktop_controller


class FakeConversation:
    def send(self, message, **kwargs):
        return {"status": "completed", "answer": "reply"}

    def set_memory_context_enabled(self, enabled):
        self.enabled = enabled

    def memory_context_enabled(self):
        return bool(getattr(self, "enabled", False))


class FakeSession:
    def __init__(self):
        self.conversation = FakeConversation()
        self.close_count = 0

    def close(self):
        self.close_count += 1


class SpecialistController:
    def __init__(self):
        self.logout_count = 0

    def logout(self):
        self.logout_count += 1
        return True


def add_specialist_contract(target):
    names = (
        "list_specialists", "propose_agent_plan", "get_agent_plan",
        "dispatch_agent_plan", "cancel_agent_plan", "list_tasks",
        "get_task", "task_progress", "approve_task", "cancel_task",
        "preview_migration", "migrate_data", "create_backup", "restore_backup",
    )
    for name in names:
        setattr(target, name, lambda *args, _name=name, **kwargs: _name)


class SharedDesktopCompositionTests(unittest.TestCase):
    @patch("ui.desktop.shared_desktop_composition.build_authenticated_desktop_conversation")
    def test_composes_durable_chat_and_preserves_specialist_controller(self, build):
        session = FakeSession()
        build.return_value = session
        specialists = SpecialistController()
        add_specialist_contract(specialists)

        bridge = build_shared_desktop_controller(
            {"workspace": "/tmp/yuki"},
            provider=object(), identity=object(), sessions=object(),
            specialist_controller=specialists, memory=object(),
        )

        self.assertEqual(bridge.respond("hello"), "reply")
        bridge.set_memory_context(True)
        self.assertTrue(bridge.memory_context_enabled())
        self.assertEqual(bridge.list_specialists(), "list_specialists")
        self.assertTrue(bridge.logout())
        self.assertEqual(session.close_count, 1)
        self.assertEqual(specialists.logout_count, 1)
        build.assert_called_once()

    @patch("ui.desktop.shared_desktop_composition.build_authenticated_desktop_conversation")
    def test_invalid_specialist_contract_closes_new_conversation_session(self, build):
        session = FakeSession()
        build.return_value = session

        with self.assertRaises(TypeError):
            build_shared_desktop_controller(
                {}, provider=object(), identity=object(), sessions=object(),
                specialist_controller=object(),
            )

        self.assertEqual(session.close_count, 1)

    @patch("ui.desktop.shared_desktop_composition.build_authenticated_desktop_conversation")
    def test_composition_passes_owner_and_storage_dependencies_unchanged(self, build):
        session = FakeSession()
        build.return_value = session
        specialists = SpecialistController()
        add_specialist_contract(specialists)
        config = {"storage_backend": "sqlite"}
        provider = object()
        identity = object()
        sessions = object()
        memory = object()
        store_factory = lambda _config: None

        build_shared_desktop_controller(
            config, provider=provider, identity=identity, sessions=sessions,
            specialist_controller=specialists, memory=memory,
            store_factory=store_factory,
        )

        kwargs = build.call_args.kwargs
        self.assertIs(kwargs["provider"], provider)
        self.assertIs(kwargs["identity"], identity)
        self.assertIs(kwargs["sessions"], sessions)
        self.assertIs(kwargs["memory"], memory)
        self.assertIs(kwargs["store_factory"], store_factory)
        self.assertIs(build.call_args.args[0], config)


if __name__ == "__main__":
    unittest.main()
