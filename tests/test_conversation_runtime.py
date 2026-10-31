import tempfile
import unittest
from pathlib import Path

from core.conversation_runtime import ConversationRuntime
from core.conversation_store import ConversationStore


class FakeProvider:
    def __init__(self, answer="hello", available=True, error=None):
        self.answer = answer
        self.available = available
        self.error = error
        self.prompts = []
        self.calls = 0

    def is_available(self):
        return self.available

    def generate(self, prompt):
        self.calls += 1
        self.prompts.append(prompt)
        if self.error:
            raise self.error
        return self.answer


class ConversationRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = ConversationStore(Path(self.temp.name) / "conversations.sqlite3")
        self.provider = FakeProvider()
        self.runtime = ConversationRuntime(self.store, self.provider)

    def test_creates_session_persists_turn_and_returns_correlated_ids(self):
        result = self.runtime.run_turn(workspace_id="workspace-a", user_message="Hello Yuki")
        self.assertTrue(result.ok)
        self.assertEqual(result.message, "hello")
        self.assertTrue(result.session_id)
        self.assertTrue(result.request_id)
        self.assertEqual(self.provider.calls, 1)
        messages = self.store.list_messages("workspace-a", result.session_id)
        self.assertEqual([m["role"] for m in messages], ["user", "assistant"])
        turn = self.store.list_turns("workspace-a", result.session_id)[0]
        self.assertEqual(turn["status"], "completed")

    def test_existing_session_context_is_reused_across_turns(self):
        session = self.store.create_session("workspace-a", "Yuki")
        first = self.runtime.run_turn(workspace_id="workspace-a", session_id=session["id"], user_message="Project is Yuki")
        second = self.runtime.run_turn(workspace_id="workspace-a", session_id=session["id"], user_message="What is the project?")
        self.assertTrue(first.ok and second.ok)
        self.assertIn("Project is Yuki", self.provider.prompts[-1])
        self.assertIn("hello", self.provider.prompts[-1])
        self.assertEqual(len(self.store.list_messages("workspace-a", session["id"])), 4)

    def test_completed_request_replay_returns_saved_answer_without_second_provider_call(self):
        session = self.store.create_session("workspace-a")
        first = self.runtime.run_turn(
            workspace_id="workspace-a", session_id=session["id"],
            user_message="same request", request_id="stable-id",
        )
        replay = self.runtime.run_turn(
            workspace_id="workspace-a", session_id=session["id"],
            user_message="same request", request_id="stable-id",
        )
        self.assertEqual(first.message, replay.message)
        self.assertTrue(replay.ok)
        self.assertEqual(self.provider.calls, 1)

    def test_running_request_replay_does_not_duplicate_provider_call(self):
        session = self.store.create_session("workspace-a")
        self.store.begin_turn("workspace-a", session["id"], "running-id", "pending")
        result = self.runtime.run_turn(
            workspace_id="workspace-a", session_id=session["id"],
            user_message="pending", request_id="running-id",
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.error_code, "turn_already_running")
        self.assertEqual(self.provider.calls, 0)

    def test_failed_turn_is_persisted_for_retry_with_new_request_id(self):
        self.provider.error = RuntimeError("provider unavailable")
        result = self.runtime.run_turn(workspace_id="workspace-a", user_message="try")
        self.assertFalse(result.ok)
        self.assertEqual(result.error_code, "provider_error")
        self.assertEqual(self.store.list_turns("workspace-a", result.session_id)[0]["status"], "failed")

    def test_unavailable_provider_fails_turn_without_generate(self):
        self.provider.available = False
        result = self.runtime.run_turn(workspace_id="workspace-a", user_message="try")
        self.assertEqual(result.error_code, "provider_unavailable")
        self.assertEqual(self.provider.calls, 0)

    def test_context_and_message_limits_are_enforced(self):
        with self.assertRaisesRegex(ValueError, "character limit"):
            ConversationRuntime(self.store, self.provider, max_user_chars=2).run_turn(
                workspace_id="workspace-a", user_message="long"
            )
        with self.assertRaisesRegex(ValueError, "context budget"):
            ConversationRuntime(self.store, self.provider, max_context_chars=20).run_turn(
                workspace_id="workspace-a", user_message="hello"
            )


if __name__ == "__main__":
    unittest.main()
