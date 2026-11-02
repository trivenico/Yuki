import tempfile
import unittest
from pathlib import Path

from automation.agent_sessions import AgentSessionStore
from automation.conversation_service import ConversationService
from automation.conversation_runtime import ConversationGenerationError, ConversationRuntime


class FakeProvider:
    def __init__(self, reply="Hello from Yuki", available=True, fail=False):
        self.reply, self.available, self.fail = reply, available, fail
        self.prompts = []

    def is_available(self):
        return self.available

    def generate(self, prompt):
        self.prompts.append(prompt)
        if self.fail:
            raise RuntimeError("provider unavailable")
        return self.reply


class ConversationRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        store = AgentSessionStore(Path(self.tmp.name) / "runtime.sqlite3",
            authorize=lambda actor, action, resource: actor == "alice")
        self.conversations = ConversationService(store)
        self.session = self.conversations.start(actor_id="alice",
            idempotency_key="start", title="Yuki", agent_id="general")
        self.runtime = ConversationRuntime(self.conversations)

    def test_generates_and_persists_user_and_assistant(self):
        provider = FakeProvider()
        reply = self.runtime.respond(actor_id="alice", session_id=self.session.session_id,
            idempotency_key="turn-1", user_message="What is Yuki?", provider=provider)
        self.assertEqual(reply, "Hello from Yuki")
        events = self.conversations.history(actor_id="alice", session_id=self.session.session_id)
        self.assertEqual([e.kind for e in events],
            ["session.created", "message.user", "message.assistant"])
        self.assertIn("USER: What is Yuki?", provider.prompts[0])

    def test_provider_failure_is_audited_without_fallback(self):
        provider = FakeProvider(fail=True)
        with self.assertRaises(ConversationGenerationError):
            self.runtime.respond(actor_id="alice", session_id=self.session.session_id,
                idempotency_key="turn-fail", user_message="Try this", provider=provider)
        events = self.conversations.history(actor_id="alice", session_id=self.session.session_id)
        self.assertEqual(events[-1].kind, "generation.failed")
        self.assertEqual(len(provider.prompts), 1)

    def test_unavailable_provider_is_not_called(self):
        provider = FakeProvider(available=False)
        with self.assertRaises(ConversationGenerationError):
            self.runtime.respond(actor_id="alice", session_id=self.session.session_id,
                idempotency_key="turn-offline", user_message="Hi", provider=provider)
        self.assertEqual(provider.prompts, [])

    def test_actor_authorization_applies_to_generation(self):
        with self.assertRaises(PermissionError):
            self.runtime.respond(actor_id="mallory", session_id=self.session.session_id,
                idempotency_key="turn-unauthorized", user_message="Hi", provider=FakeProvider())

    def test_provider_contract_is_checked(self):
        with self.assertRaises(TypeError):
            self.runtime.respond(actor_id="alice", session_id=self.session.session_id,
                idempotency_key="turn-bad", user_message="Hi", provider=object())


if __name__ == "__main__":
    unittest.main()
