import tempfile
import unittest
from pathlib import Path

from core.application_runtime import YukiApplicationRuntime
from core.conversation_store import ConversationStore
from providers import MockProvider
from yuki_agent import YukiAgent


class FakeProviders:
    def __init__(self):
        self.adapter = MockProvider()

    def create_selected_adapter(self):
        return self.adapter


class ApplicationRuntimeTests(unittest.TestCase):
    def test_shared_runtime_composes_provider_agent_and_persistent_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime = YukiApplicationRuntime.create(
                providers_factory=FakeProviders,
                agent_factory=YukiAgent,
                conversation_factory=lambda: ConversationStore(Path(tmp) / "chat.sqlite3"),
            )
            conversation_id = runtime.active_conversation_id
            self.assertIs(runtime.agent.provider, runtime.providers.adapter)
            runtime.begin_turn("Hello from Windows")
            runtime.finish_turn("Hello from Yuki")
            self.assertEqual(
                [(item["role"], item["content"]) for item in runtime.history()],
                [("user", "Hello from Windows"), ("assistant", "Hello from Yuki")],
            )
            runtime.close()
            with self.assertRaises(RuntimeError):
                runtime.history()
            reopened = ConversationStore(Path(tmp) / "chat.sqlite3")
            try:
                self.assertEqual(
                    [item["content"] for item in reopened.messages(conversation_id)],
                    ["Hello from Windows", "Hello from Yuki"],
                )
            finally:
                reopened.close()

    def test_new_conversation_switches_active_history_without_losing_old_chat(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime = YukiApplicationRuntime.create(
                providers_factory=FakeProviders,
                conversation_factory=lambda: ConversationStore(Path(tmp) / "chat.sqlite3"),
            )
            first = runtime.active_conversation_id
            runtime.begin_turn("First chat")
            second = runtime.new_conversation("Second chat")
            self.assertNotEqual(first, second)
            runtime.begin_turn("Second chat message")
            self.assertEqual([m["content"] for m in runtime.history()], ["Second chat message"])
            self.assertEqual(
                [m["content"] for m in runtime.conversations.messages(first)],
                ["First chat"],
            )
            runtime.close()

    def test_shared_send_message_runs_provider_and_persists_both_turns(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime = YukiApplicationRuntime.create(
                providers_factory=FakeProviders,
                conversation_factory=lambda: ConversationStore(Path(tmp) / "chat.sqlite3"),
            )
            response = runtime.send_message("hello")
            self.assertIn("hello", response)
            self.assertEqual(
                [(item["role"], item["content"]) for item in runtime.history()],
                [("user", "hello"), ("assistant", response)],
            )
            runtime.close()

    def test_switch_conversation_changes_active_runtime_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime = YukiApplicationRuntime.create(
                providers_factory=FakeProviders,
                conversation_factory=lambda: ConversationStore(Path(tmp) / "chat.sqlite3"),
            )
            first = runtime.active_conversation_id
            second = runtime.new_conversation("Second")
            runtime.switch_conversation(first)
            self.assertEqual(runtime.active_conversation_id, first)
            with self.assertRaises(ValueError):
                runtime.switch_conversation("not-a-real-conversation")
            runtime.close()

    def test_runtime_closes_store_if_session_creation_fails(self):
        closed = []

        class BrokenStore:
            def create(self, *_):
                raise RuntimeError("database unavailable")
            def close(self):
                closed.append(True)

        with self.assertRaises(RuntimeError):
            YukiApplicationRuntime.create(
                providers_factory=FakeProviders,
                conversation_factory=BrokenStore,
            )
        self.assertEqual(closed, [True])

    def test_rejects_blank_turns_and_closed_runtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime = YukiApplicationRuntime.create(
                providers_factory=FakeProviders,
                conversation_factory=lambda: ConversationStore(Path(tmp) / "chat.sqlite3"),
            )
            with self.assertRaises(ValueError):
                runtime.begin_turn("  ")
            runtime.close()
            with self.assertRaises(RuntimeError):
                runtime.begin_turn("Hello")


if __name__ == "__main__":
    unittest.main()
