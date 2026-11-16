"""Tests for authenticated desktop conversation session enforcement."""
from dataclasses import dataclass
import tempfile
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

    def build(self, model):
        class DenyAll:
            @staticmethod
            def authorize_tool(name):
                return False

        loop = AgentExecutionLoop(
            model, StructuredToolProtocol(GuardedToolRuntime({}, DenyAll())),
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
