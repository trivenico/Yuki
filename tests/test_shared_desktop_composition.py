"""Tests for desktop composition across durable chat and specialist APIs."""
import unittest
from unittest.mock import patch

from ui.desktop.shared_desktop_composition import build_shared_desktop_controller


class FakeConversation:
    def __init__(self):
        self.selected = "conversation-1"

    def send(self, message, **kwargs):
        return {"status": "completed", "answer": "reply"}

    def start_new_conversation(self, *, title="New conversation"):
        self.selected = "conversation-new"
        return {"conversation_id": self.selected, "title": title, "version": 0}

    def select_conversation(self, conversation_id):
        if conversation_id == "foreign-owner-conversation":
            raise PermissionError("not owned by this account")
        self.selected = conversation_id
        return {"conversation_id": conversation_id, "title": "Test", "version": 1}

    def list_conversations(self, *, limit=50):
        return [{"conversation_id": "conversation-1", "title": "Existing"}][:limit]

    def history(self, *, limit=100):
        return [{"role": "user", "content": "hello"}][:limit]

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
    def test_bridge_exposes_owner_scoped_conversation_navigation(self, build):
        session = FakeSession()
        build.return_value = session
        specialists = SpecialistController()
        add_specialist_contract(specialists)
        bridge = build_shared_desktop_controller(
            {}, provider=object(), identity=object(), sessions=object(),
            specialist_controller=specialists,
        )

        created = bridge.start_new_conversation(title="Fresh")
        self.assertEqual(created["conversation_id"], "conversation-new")
        self.assertEqual(bridge.list_conversations()[0]["conversation_id"], "conversation-1")
        selected = bridge.select_conversation("conversation-1")
        self.assertEqual(selected["conversation_id"], "conversation-1")
        self.assertEqual(bridge.history()[0]["content"], "hello")

    @patch("ui.desktop.shared_desktop_composition.build_authenticated_desktop_conversation")
    def test_owner_scope_failure_closes_bridge_and_both_sessions(self, build):
        session = FakeSession()
        build.return_value = session
        specialists = SpecialistController()
        add_specialist_contract(specialists)
        bridge = build_shared_desktop_controller(
            {}, provider=object(), identity=object(), sessions=object(),
            specialist_controller=specialists,
        )

        with self.assertRaises(PermissionError):
            bridge.select_conversation("foreign-owner-conversation")
        self.assertEqual(session.close_count, 1)
        self.assertEqual(specialists.logout_count, 1)
        with self.assertRaisesRegex(RuntimeError, "closed"):
            bridge.history()

    @patch("ui.desktop.shared_desktop_composition.build_authenticated_desktop_conversation")
    def test_passes_curated_knowledge_provider_to_authenticated_chat(self, build):
        session = FakeSession()
        build.return_value = session
        specialists = SpecialistController()
        add_specialist_contract(specialists)
        knowledge_provider = lambda owner, query: ()

        build_shared_desktop_controller(
            {}, provider=object(), identity=object(), sessions=object(),
            specialist_controller=specialists,
            knowledge_entries_for_owner=knowledge_provider,
        )

        self.assertIs(
            build.call_args.kwargs["knowledge_entries_for_owner"], knowledge_provider
        )

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
    def test_failed_chat_composition_logs_out_existing_specialist_controller(self, build):
        specialists = SpecialistController()
        add_specialist_contract(specialists)
        error = RuntimeError("store initialization failed")
        build.side_effect = error

        with self.assertRaisesRegex(RuntimeError, "store initialization failed"):
            build_shared_desktop_controller(
                {}, provider=object(), identity=object(), sessions=object(),
                specialist_controller=specialists, memory=object(),
            )

        self.assertEqual(specialists.logout_count, 1)

    @patch("ui.desktop.shared_desktop_composition.build_authenticated_desktop_conversation")
    def test_bridge_validation_failure_closes_chat_and_logs_out_specialist(self, build):
        session = FakeSession()
        build.return_value = session
        specialists = SpecialistController()

        with self.assertRaises(TypeError):
            build_shared_desktop_controller(
                {}, provider=object(), identity=object(), sessions=object(),
                specialist_controller=specialists, memory=object(),
            )

        self.assertEqual(session.close_count, 1)
        self.assertEqual(specialists.logout_count, 1)

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
