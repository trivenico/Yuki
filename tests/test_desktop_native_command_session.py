import unittest
from dataclasses import dataclass
from unittest.mock import Mock, patch

from core.native_command_runtime import NativeCommandRuntime
from ui.desktop.native_command_session import AuthenticatedNativeCommandSession
from ui.desktop.shared_desktop_composition import build_shared_desktop_controller


@dataclass(frozen=True)
class Identity:
    actor_id: str = "actor-1"
    session_id: str = "session-1"


class Sessions:
    def __init__(self, identity):
        self.identity = identity

    def current_identity(self):
        return self.identity


class Runtime:
    def __init__(self):
        self.calls = []

    def dispatch(self, **kwargs):
        self.calls.append(kwargs)
        return {"kind": "help", "executed": True}


class FakeConversation:
    def send(self, message, **kwargs):
        return {"status": "completed", "answer": "reply"}

    def list_conversations(self, *, limit=50):
        return []

    def select_conversation(self, conversation_id):
        return {"conversation_id": conversation_id}

    def history(self, *, limit=100):
        return []

    def set_memory_context_enabled(self, enabled):
        pass

    def memory_context_enabled(self):
        return False


class FakeSession:
    def __init__(self):
        self.conversation = FakeConversation()

    def close(self):
        pass


class Specialists:
    def logout(self):
        return True


def specialist_contract():
    value = Specialists()
    for name in (
        "list_specialists", "propose_agent_plan", "get_agent_plan",
        "dispatch_agent_plan", "cancel_agent_plan", "list_tasks", "get_task",
        "task_progress", "approve_task", "cancel_task", "preview_migration",
        "migrate_data", "create_backup", "restore_backup",
    ):
        setattr(value, name, lambda *args, **kwargs: None)
    return value


class AuthenticatedNativeCommandSessionTests(unittest.TestCase):
    def test_dispatch_uses_authenticated_identity_not_client_argument(self):
        identity = Identity()
        sessions = Sessions(identity)
        runtime = Mock(spec=NativeCommandRuntime)
        runtime.dispatch.return_value = "ok"
        facade = AuthenticatedNativeCommandSession(runtime, identity=identity, sessions=sessions)
        self.assertEqual(facade.dispatch("/help"), "ok")
        runtime.dispatch.assert_called_once_with(
            authenticated_actor_id="actor-1", text="/help", confirmed=False,
        )

    def test_revoked_session_blocks_dispatch(self):
        identity = Identity()
        sessions = Sessions(identity)
        runtime = Mock(spec=NativeCommandRuntime)
        facade = AuthenticatedNativeCommandSession(runtime, identity=identity, sessions=sessions)
        sessions.identity = None
        with self.assertRaises(PermissionError):
            facade.dispatch("/help")
        runtime.dispatch.assert_not_called()

    @patch("ui.desktop.shared_desktop_composition.build_authenticated_desktop_conversation")
    def test_desktop_composition_exposes_session_bound_native_dispatch(self, build):
        identity = Identity()
        sessions = Sessions(identity)
        runtime = Runtime()
        build.return_value = FakeSession()
        bridge = build_shared_desktop_controller(
            {}, provider=object(), identity=identity, sessions=sessions,
            specialist_controller=specialist_contract(),
            native_command_runtime=Mock(spec=NativeCommandRuntime),
        )
        result = bridge.dispatch_native_command("/help")
        self.assertEqual(result, "ok")  # supplied runtime is the mocked contract
        bridge.logout()

    @patch("ui.desktop.shared_desktop_composition.build_authenticated_desktop_conversation")
    def test_native_dispatch_is_unavailable_when_not_composed(self, build):
        build.return_value = FakeSession()
        bridge = build_shared_desktop_controller(
            {}, provider=object(), identity=object(), sessions=object(),
            specialist_controller=specialist_contract(),
        )
        with self.assertRaisesRegex(RuntimeError, "not configured"):
            bridge.dispatch_native_command("/help")
        bridge.logout()


if __name__ == "__main__":
    unittest.main()
