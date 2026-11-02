import tempfile
import unittest
from pathlib import Path
from threading import Event
from automation.agent_sessions import AgentSessionStore
from automation.conversation_service import ConversationService
from automation.conversation_runtime import ConversationGenerationError, ConversationRuntime


class StreamProvider:
    def __init__(self, chunks=("hello ", "from Yuki"), fail=False):
        self.chunks, self.fail = chunks, fail
    def is_available(self):
        return True
    def stream(self, prompt):
        if self.fail:
            raise RuntimeError("provider failed")
        for chunk in self.chunks:
            yield chunk


class StreamingConversationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        store = AgentSessionStore(Path(self.tmp.name) / "stream.sqlite3",
            authorize=lambda actor, action, resource: actor == "alice")
        self.service = ConversationService(store)
        self.session = self.service.start(actor_id="alice", idempotency_key="start",
            title="Yuki", agent_id="general")
        self.runtime = ConversationRuntime(self.service)
    def stream(self, key, provider, cancel=None):
        return list(self.runtime.respond_stream(actor_id="alice",
            session_id=self.session.session_id, idempotency_key=key,
            user_message="Hello", provider=provider, cancel_event=cancel))
    def history(self):
        return self.service.history(actor_id="alice", session_id=self.session.session_id)
    def test_completed_stream_persists_one_assistant_message(self):
        events = self.stream("ok", StreamProvider())
        self.assertEqual([e.type for e in events], ["delta", "delta", "completed"])
        assistants = [e for e in self.history() if e.kind == "message.assistant"]
        self.assertEqual(len(assistants), 1)
        self.assertEqual(assistants[0].payload["content"], "hello from Yuki")
    def test_cancelled_stream_never_persists_partial_reply(self):
        cancel = Event()
        class Canceller(StreamProvider):
            def stream(self, prompt):
                yield "partial"
                cancel.set()
                yield "discard"
        events = self.stream("cancel", Canceller(), cancel)
        self.assertEqual([e.type for e in events], ["delta", "interrupted"])
        self.assertFalse(any(e.kind == "message.assistant" for e in self.history()))
        self.assertEqual(self.history()[-1].kind, "generation.interrupted")
    def test_failed_stream_is_audited_without_partial_reply(self):
        with self.assertRaises(ConversationGenerationError):
            self.stream("fail", StreamProvider(fail=True))
        self.assertEqual(self.history()[-1].kind, "generation.failed")
        self.assertFalse(any(e.kind == "message.assistant" for e in self.history()))
    def test_non_streaming_provider_is_rejected(self):
        with self.assertRaises(TypeError):
            self.stream("bad", object())


if __name__ == "__main__":
    unittest.main()
