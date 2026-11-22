"""Regression tests for authenticated desktop memory-saving consent."""
from __future__ import annotations

import unittest
from threading import Condition, RLock

from ui.desktop.authenticated_conversation import AuthenticatedDesktopConversation
from ui.desktop.shared_chat_controller import SharedDesktopChatController
from ui.desktop.shared_desktop_controller import SharedDesktopControllerBridge


class _Identity:
    actor_id = "alice"


class _Sessions:
    def __init__(self, identity):
        self.identity = identity

    def current_identity(self):
        return self.identity


class _Memory:
    def __init__(self):
        self.enabled = False
        self.context_enabled = False

    def enable(self):
        self.enabled = True

    def disable(self):
        self.enabled = False
        self.context_enabled = False

    def invalidate(self):
        self.disable()
        self.invalidated = True

    def list_memories(self):
        if getattr(self, "invalidated", False):
            raise RuntimeError("memory facade invalidated")
        return []

    def enable_context(self):
        self.context_enabled = True

    def disable_context(self):
        self.context_enabled = False


class _Conversation:
    def __init__(self):
        self.saving_enabled = False
        self.context_enabled = False

    def set_memory_saving_enabled(self, enabled):
        self.saving_enabled = enabled

    def memory_saving_enabled(self):
        return self.saving_enabled

    def set_memory_context_enabled(self, enabled):
        self.context_enabled = enabled

    def memory_context_enabled(self):
        return self.context_enabled

    def send(self, *args, **kwargs):
        return {"status": "completed", "answer": "ok"}

    def list_conversations(self, **kwargs):
        return []

    def select_conversation(self, conversation_id):
        return {"conversation_id": conversation_id}

    def history(self, **kwargs):
        return []

    def clear_selection(self):
        pass


class _Session:
    def __init__(self, conversation):
        self.conversation = conversation

    def close(self):
        pass


class _Specialists:
    def logout(self):
        return True

    def __getattr__(self, name):
        return lambda *args, **kwargs: None


class AuthenticatedDesktopMemoryConsentTests(unittest.TestCase):
    def _facade(self, memory, sessions, identity):
        facade = AuthenticatedDesktopConversation.__new__(AuthenticatedDesktopConversation)
        facade._identity = identity
        facade._sessions = sessions
        facade._memory = memory
        facade._invalidated = False
        facade._lifecycle = Condition(RLock())
        facade._active_sends = 0
        facade._conversation = _Conversation()
        return facade

    def test_authenticated_facade_enables_and_disables_explicit_saving(self):
        identity = _Identity()
        memory = _Memory()
        facade = self._facade(memory, _Sessions(identity), identity)

        self.assertFalse(facade.memory_saving_enabled())
        facade.set_memory_saving_enabled(True)
        self.assertTrue(facade.memory_saving_enabled())
        self.assertTrue(memory.enabled)
        facade.set_memory_saving_enabled(False)
        self.assertFalse(facade.memory_saving_enabled())
        self.assertFalse(memory.enabled)

    def test_saving_consent_requires_live_session_and_boolean(self):
        identity = _Identity()
        sessions = _Sessions(identity)
        facade = self._facade(_Memory(), sessions, identity)
        with self.assertRaises(ValueError):
            facade.set_memory_saving_enabled(1)
        sessions.identity = None
        with self.assertRaises(PermissionError):
            facade.set_memory_saving_enabled(True)
        with self.assertRaises(PermissionError):
            facade.memory_saving_enabled()

    def test_invalidation_revokes_saving_and_context_consent(self):
        identity = _Identity()
        memory = _Memory()
        memory.enable()
        memory.enable_context()
        facade = self._facade(memory, _Sessions(identity), identity)

        facade.invalidate()

        self.assertFalse(memory.enabled)
        self.assertFalse(memory.context_enabled)
        self.assertTrue(facade._invalidated)
        self.assertTrue(memory.invalidated)
        with self.assertRaises(RuntimeError):
            memory.list_memories()

    def test_desktop_bridge_routes_memory_saving_toggle_to_authenticated_facade(self):
        conversation = _Conversation()
        chat = SharedDesktopChatController(_Session(conversation))
        bridge = SharedDesktopControllerBridge(chat, _Specialists())

        bridge.set_memory_saving_enabled(True)
        self.assertTrue(bridge.memory_saving_enabled())
        bridge.set_memory_saving_enabled(False)
        self.assertFalse(bridge.memory_saving_enabled())

        bridge.logout()


if __name__ == "__main__":
    unittest.main()
