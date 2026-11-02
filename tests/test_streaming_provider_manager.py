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
    def is_available(self): return True
    def stream(self, prompt):
        if self.fail: raise RuntimeError("provider failed")
        for chunk in self.chunks: yield chunk


class Manager:
    def __init__(self, provider): self.provider, self.calls = provider, []
    def create_routed_adapter(self, **kwargs):
        self.calls.append(kwargs)
        return self.provider


class StreamingProviderManagerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        store = AgentSessionStore(Path(self.tmp.name) / "stream.sqlite3",
            authorize=lambda actor, action, resource: actor == "alice")
        self.service = ConversationService(store)
        self.session = self.service.start(actor_id="alice", idempotency_key="start",
            title="Yuki", agent_id="general")
        self.runtime = ConversationRuntime(self.service)
    def test_manager_routes_with_explicit_cloud_consent(self):
        provider, manager = StreamProvider(), None
        manager = Manager(provider)
        events = list(self.runtime.respond_stream_with_manager(
            actor_id="alice", session_id=self.session.session_id, idempotency_key="routed",
            user_message="Hello", provider_manager=manager, allow_cloud=True,
            preference=("ollama", "openai"),
        ))
        self.assertEqual([e.type for e in events], ["delta", "delta", "completed"])
        self.assertEqual(manager.calls, [{
            "required": ("text",), "preference": ("ollama", "openai"), "allow_cloud": True,
        }])
    def test_manager_route_defaults_to_no_cloud(self):
        manager = Manager(StreamProvider())
        list(self.runtime.respond_stream_with_manager(
            actor_id="alice", session_id=self.session.session_id, idempotency_key="local",
            user_message="Hello", provider_manager=manager,
        ))
        self.assertFalse(manager.calls[0]["allow_cloud"])
    def test_unauthorized_session_does_not_discover_provider(self):
        manager = Manager(StreamProvider())
        with self.assertRaises(PermissionError):
            list(self.runtime.respond_stream_with_manager(
                actor_id="mallory", session_id=self.session.session_id, idempotency_key="bad",
                user_message="Hello", provider_manager=manager,
            ))
        self.assertEqual(manager.calls, [])
    def test_consumer_close_records_interruption_and_closes_provider(self):
        closed = []
        class ClosingProvider(StreamProvider):
            def stream(self, prompt):
                try:
                    yield "partial"
                    yield "more"
                finally:
                    closed.append(True)
        iterator = self.runtime.respond_stream(
            actor_id="alice", session_id=self.session.session_id, idempotency_key="close",
            user_message="Hello", provider=ClosingProvider(),
        )
        self.assertEqual(next(iterator).content, "partial")
        iterator.close()
        history = self.service.history(actor_id="alice", session_id=self.session.session_id)
        self.assertEqual(history[-1].kind, "generation.interrupted")
        self.assertEqual(history[-1].payload["reason"], "consumer_closed")
        self.assertEqual(closed, [True])
        self.assertFalse(any(e.kind == "message.assistant" for e in history))
    def test_manager_rejects_provider_without_streaming(self):
        with self.assertRaises(TypeError):
            list(self.runtime.respond_stream_with_manager(
                actor_id="alice", session_id=self.session.session_id, idempotency_key="nostream",
                user_message="Hello", provider_manager=Manager(object()),
            ))


if __name__ == "__main__": unittest.main()
