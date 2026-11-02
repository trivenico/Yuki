import tempfile
import unittest
from pathlib import Path

from automation.agent_sessions import AgentSessionStore
from automation.conversation_service import ConversationService
from automation.conversation_runtime import ConversationGenerationError, ConversationRuntime
from yuki_providers import ProviderManager


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

    def test_prompt_uses_latest_events_for_long_conversations(self):
        for index in range(110):
            self.conversations.add_message(
                actor_id="alice", session_id=self.session.session_id,
                idempotency_key=f"history-{index}", role="user",
                content=f"message-{index}",
            )
        prompt = self.runtime.build_prompt(actor_id="alice", session_id=self.session.session_id)
        self.assertIn("message-109", prompt)
        self.assertNotIn("message-0", prompt)

    def test_prompt_budget_keeps_latest_user_message(self):
        huge = "x" * 32_000
        for index in range(4):
            self.conversations.add_message(
                actor_id="alice", session_id=self.session.session_id,
                idempotency_key=f"large-{index}", role="assistant", content=huge,
            )
        self.conversations.add_message(
            actor_id="alice", session_id=self.session.session_id,
            idempotency_key="latest-question", role="user", content="LATEST QUESTION",
        )
        prompt = self.runtime.build_prompt(actor_id="alice", session_id=self.session.session_id)
        self.assertLessEqual(len(prompt), 100_000)
        self.assertIn("LATEST QUESTION", prompt)
        self.assertTrue(prompt.endswith("USER: LATEST QUESTION"))

    def test_provider_manager_route_passes_cloud_consent_gate(self):
        class Manager:
            def __init__(self):
                self.calls = []
            def create_routed_adapter(self, **kwargs):
                self.calls.append(kwargs)
                return FakeProvider("Routed response")
        manager = Manager()
        reply = self.runtime.respond_with_manager(
            actor_id="alice", session_id=self.session.session_id,
            idempotency_key="turn-routed", user_message="Route this",
            provider_manager=manager, preference=("openai",), allow_cloud=False,
        )
        self.assertEqual(reply, "Routed response")
        self.assertEqual(manager.calls, [{
            "required": ("text",), "preference": ("openai",), "allow_cloud": False,
        }])

    def test_real_provider_manager_routes_local_adapter(self):
        manager = ProviderManager(
            config={"provider": "ollama", "ollama_model": "test-model"},
            environ={"YUKI_OLLAMA_MODEL": "test-model"},
        )
        manager._build_adapter = lambda name: FakeProvider("Actual manager route")
        reply = self.runtime.respond_with_manager(
            actor_id="alice", session_id=self.session.session_id,
            idempotency_key="turn-real-manager", user_message="Use local model",
            provider_manager=manager, preference=("ollama",), allow_cloud=False,
        )
        self.assertEqual(reply, "Actual manager route")

    def test_unauthorized_actor_is_rejected_before_provider_routing(self):
        class Manager:
            def __init__(self):
                self.called = False
            def create_routed_adapter(self, **kwargs):
                self.called = True
                return FakeProvider()
        manager = Manager()
        with self.assertRaises(PermissionError):
            self.runtime.respond_with_manager(
                actor_id="mallory", session_id=self.session.session_id,
                idempotency_key="unauthorized-route", user_message="No",
                provider_manager=manager,
            )
        self.assertFalse(manager.called)

    def test_provider_manager_contract_is_required(self):
        with self.assertRaises(TypeError):
            self.runtime.respond_with_manager(
                actor_id="alice", session_id=self.session.session_id,
                idempotency_key="turn-invalid", user_message="Hi",
                provider_manager=object(),
            )

    def test_provider_contract_is_checked(self):
        with self.assertRaises(TypeError):
            self.runtime.respond(actor_id="alice", session_id=self.session.session_id,
                idempotency_key="turn-bad", user_message="Hi", provider=object())


if __name__ == "__main__":
    unittest.main()
