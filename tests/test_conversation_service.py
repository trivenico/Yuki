import tempfile
import unittest
from pathlib import Path

from automation.agent_sessions import AgentSessionStore, SessionConflict
from automation.conversation_service import ConversationService


class ConversationServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        store = AgentSessionStore(
            Path(self.tmp.name) / "conversation.sqlite3",
            authorize=lambda actor, action, resource: actor == "alice",
        )
        self.service = ConversationService(store)
        self.session = self.service.start(
            actor_id="alice", idempotency_key="start-1",
            title="Yuki MVP", agent_id="engineering", model_id="test-model",
        )

    def test_persists_user_assistant_and_tool_events_in_order(self):
        user = self.service.add_message(
            actor_id="alice", session_id=self.session.session_id,
            idempotency_key="user-1", role="user", content="Inspect the failing tests",
        )
        assistant = self.service.add_message(
            actor_id="alice", session_id=self.session.session_id,
            idempotency_key="assistant-1", role="assistant", content="I will inspect them.",
        )
        tool = self.service.record_tool_event(
            actor_id="alice", session_id=self.session.session_id,
            idempotency_key="tool-1", call_id="call-1", tool_name="test_runner",
            status="completed", summary="12 passed, 1 failed",
        )
        self.assertEqual([user.sequence, assistant.sequence, tool.sequence], [2, 3, 4])
        events = self.service.history(actor_id="alice", session_id=self.session.session_id)
        self.assertEqual([event.kind for event in events],
                         ["session.created", "message.user", "message.assistant", "tool.completed"])

    def test_history_rejects_other_actor(self):
        with self.assertRaises(PermissionError):
            self.service.history(actor_id="mallory", session_id=self.session.session_id)

    def test_rejects_invalid_role_and_tool_status(self):
        with self.assertRaises(ValueError):
            self.service.add_message(actor_id="alice", session_id=self.session.session_id,
                idempotency_key="bad-role", role="developer", content="no")
        with self.assertRaises(ValueError):
            self.service.record_tool_event(actor_id="alice", session_id=self.session.session_id,
                idempotency_key="bad-tool", call_id="1", tool_name="shell",
                status="execute", summary="not a supported event")

    def test_idempotent_message_retry(self):
        args = dict(actor_id="alice", session_id=self.session.session_id,
            idempotency_key="same-message", role="user", content="Hello")
        first = self.service.add_message(**args)
        second = self.service.add_message(**args)
        self.assertEqual(first.event_id, second.event_id)
        with self.assertRaises(SessionConflict):
            self.service.add_message(**{**args, "content": "Changed"})

    def test_message_size_is_bounded(self):
        with self.assertRaises(ValueError):
            self.service.add_message(actor_id="alice", session_id=self.session.session_id,
                idempotency_key="too-large", role="user", content="x" * 32001)

if __name__ == "__main__":
    unittest.main()
