"""Tests for authenticated desktop conversation session enforcement."""
from dataclasses import dataclass
import tempfile
import threading
import unittest
from pathlib import Path

from core.agent_execution_loop import AgentExecutionLoop
from core.conversation_application_runtime import build_conversation_application_runtime
from core.conversation_store import ConversationStore
from core.guarded_tool_runtime import GuardedToolRuntime
from core.structured_tool_protocol import StructuredToolProtocol
from ui.desktop.authenticated_conversation import AuthenticatedDesktopConversation


@dataclass(frozen=True)
class Identity:
    actor_id: str
    session_id: str = "session-1"


class Sessions:
    def __init__(self, identity):
        self.identity = identity

    def current_identity(self):
        return self.identity


class AuthenticatedDesktopConversationTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.identity = Identity("owner-1")
        self.sessions = Sessions(self.identity)
        self.application = None

    def tearDown(self):
        if self.application is not None and not self.application.closed:
            self.application.close()
        self.tempdir.cleanup()

    def build(self, model, *, stream_model=None):
        class DenyAll:
            @staticmethod
            def authorize_tool(name):
                return False

        loop = AgentExecutionLoop(
            model, StructuredToolProtocol(GuardedToolRuntime({}, DenyAll())),
            stream_model=stream_model,
        )
        self.application = build_conversation_application_runtime(
            {"workspace": self.tempdir.name},
            agent_loop=loop,
            store_factory=lambda config: ConversationStore(
                Path(config["workspace"]) / "conversations.sqlite3"
            ),
        )
        return AuthenticatedDesktopConversation(
            self.application, identity=self.identity, sessions=self.sessions,
        )

    def test_owner_is_taken_from_authenticated_identity(self):
        facade = self.build(lambda **kwargs: {
            "text": "Hello", "tool_calls": {"tool_calls": []},
        })
        result = facade.send("Hello", request_id="authenticated-1")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(
            self.application.conversations.list(owner_id="owner-1")[0]["conversation_id"],
            result["conversation_id"],
        )

    def test_revoked_session_is_rejected_before_operations(self):
        facade = self.build(lambda **kwargs: {
            "text": "Hello", "tool_calls": {"tool_calls": []},
        })
        self.sessions.identity = None
        with self.assertRaises(PermissionError):
            facade.list_conversations()
        with self.assertRaises(PermissionError):
            facade.send("Must not execute")

    def test_response_is_not_returned_if_session_expires_during_generation(self):
        def model(**kwargs):
            self.sessions.identity = None
            return {"text": "Sensitive reply", "tool_calls": {"tool_calls": []}}

        facade = self.build(model)
        with self.assertRaises(PermissionError):
            facade.send("Run while session expires", request_id="expires-mid-turn")

    def test_revocation_is_polled_during_streaming_and_cancels_turn(self):
        stream_started = []
        def stream_model(**kwargs):
            stream_started.append(True)
            yield {"type": "text_delta", "text": "must not be delivered"}
            yield {"type": "response", "response": {
                "text": "must not be delivered",
                "tool_calls": {"tool_calls": []},
            }}

        facade = self.build(
            lambda **kwargs: {"text": "unused", "tool_calls": {"tool_calls": []}},
            stream_model=stream_model,
        )
        def revoke_during_first_cancel_poll():
            self.sessions.identity = None
            return False

        with self.assertRaises(PermissionError):
            facade.send(
                "Cancel after session revocation",
                request_id="revoked-stream",
                cancellation_requested=revoke_during_first_cancel_poll,
                on_text_delta=lambda _delta: self.fail("revoked output must not be delivered"),
            )
        self.assertEqual(stream_started, [])

    def test_memory_context_requires_live_session_and_explicit_consent(self):
        class Memory:
            def __init__(self):
                self.context_enabled = False
            def enable_context(self):
                self.context_enabled = True
            def disable_context(self):
                self.context_enabled = False

        memory = Memory()
        loop = self.build(lambda **kwargs: {
            "text": "Hello", "tool_calls": {"tool_calls": []},
        })
        facade = AuthenticatedDesktopConversation(
            self.application, identity=self.identity, sessions=self.sessions,
            memory=memory,
        )
        self.assertFalse(facade.memory_context_enabled())
        facade.set_memory_context_enabled(True)
        self.assertTrue(facade.memory_context_enabled())
        self.sessions.identity = None
        with self.assertRaises(PermissionError):
            facade.set_memory_context_enabled(False)
        # Logout can still revoke local consent after the session is revoked.
        facade.disable_memory_context()
        self.assertFalse(memory.context_enabled)

    def test_invalidate_clears_selection_and_memory_even_after_session_revocation(self):
        class Memory:
            context_enabled = True
            def disable_context(self):
                self.context_enabled = False

        facade = self.build(lambda **kwargs: {
            "text": "Hello", "tool_calls": {"tool_calls": []},
        })
        facade.start_new_conversation()
        memory = Memory()
        facade_with_memory = AuthenticatedDesktopConversation(
            self.application, identity=self.identity, sessions=self.sessions,
            memory=memory,
        )
        facade_with_memory.start_new_conversation()
        self.sessions.identity = None
        facade_with_memory.invalidate()
        self.assertFalse(memory.context_enabled)
        with self.assertRaises(PermissionError):
            facade_with_memory.list_conversations()
        self.assertIsNone(facade_with_memory._conversation.conversation_id)

    def test_invalidation_closes_authorization_gate_before_cleanup_finishes(self):
        entered = threading.Event()
        release = threading.Event()
        class BlockingMemory:
            context_enabled = True
            def disable_context(self):
                entered.set()
                release.wait(timeout=2)
                self.context_enabled = False

        facade = self.build(lambda **kwargs: {
            "text": "Hello", "tool_calls": {"tool_calls": []},
        })
        facade = AuthenticatedDesktopConversation(
            self.application, identity=self.identity, sessions=self.sessions,
            memory=BlockingMemory(),
        )
        worker = threading.Thread(target=facade.invalidate)
        worker.start()
        self.assertTrue(entered.wait(timeout=1))
        with self.assertRaises(PermissionError):
            facade.list_conversations()
        release.set()
        worker.join(timeout=2)
        self.assertFalse(worker.is_alive())

    def test_constructor_rejects_unverified_identity(self):
        class ForgedSessions:
            @staticmethod
            def current_identity():
                return None

        with self.assertRaises(PermissionError):
            AuthenticatedDesktopConversation(
                object(), identity=self.identity, sessions=ForgedSessions(),
            )


if __name__ == "__main__":
    unittest.main()
