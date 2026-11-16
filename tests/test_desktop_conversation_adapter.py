"""Tests for the desktop adapter over the canonical conversation runtime."""
import tempfile
import threading
import unittest
from pathlib import Path

from core.agent_execution_loop import AgentExecutionLoop
from core.conversation_application_runtime import build_conversation_application_runtime
from core.conversation_store import ConversationNotFound, ConversationStore
from core.guarded_tool_runtime import GuardedToolRuntime
from core.structured_tool_protocol import StructuredToolProtocol
from ui.desktop.conversation_adapter import DesktopConversationAdapter


class DenyAll:
    def authorize_tool(self, name):
        return False


class DesktopConversationAdapterTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.observed = []

        def model(*, messages, tools, tool_choice):
            self.observed.append((messages, tools))
            return {"text": "Canonical runtime reply", "tool_calls": {"tool_calls": []}}

        def stream_model(*, messages, tools, tool_choice):
            yield {"type": "text_delta", "text": "Streamed"}
            yield {
                "type": "response",
                "response": {
                    "text": "Streamed",
                    "tool_calls": {"tool_calls": []},
                },
            }

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
        self.adapter = DesktopConversationAdapter(
            self.application, owner_id="desktop-owner",
        )

    def tearDown(self):
        self.application.close()
        self.tempdir.cleanup()

    def test_send_persists_turn_through_shared_runtime_without_tools(self):
        result = self.adapter.send("Hello Yuki", request_id="desktop-request-1")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["answer"], "Canonical runtime reply")
        self.assertEqual(result["version"], 2)
        self.assertEqual(self.observed[0][1], [])
        self.assertEqual(
            [item["role"] for item in self.adapter.history()],
            ["user", "assistant"],
        )

    def test_followup_uses_persisted_transcript(self):
        self.adapter.send("First turn", request_id="desktop-request-1")
        self.adapter.send("Second turn", request_id="desktop-request-2")
        self.assertEqual(
            self.observed[1][0][1:],
            [
                {"role": "user", "content": "First turn"},
                {"role": "assistant", "content": "Canonical runtime reply"},
                {"role": "user", "content": "Second turn"},
            ],
        )

    def test_streaming_deltas_are_forwarded_by_the_adapter(self):
        deltas = []
        result = self.adapter.send(
            "Stream this", request_id="stream-request", on_text_delta=deltas.append,
        )
        self.assertEqual(result["answer"], "Streamed")
        self.assertEqual(deltas, ["Streamed"])

    def test_conversation_listing_is_owner_scoped(self):
        self.adapter.start_new_conversation(title="Mine")
        self.application.conversations.create(owner_id="another-owner", title="Not mine")
        listed = self.adapter.list_conversations()
        self.assertEqual([item["title"] for item in listed], ["Mine"])

    def test_select_conversation_enforces_owner_scope(self):
        conversation = self.application.conversations.create(owner_id="another-owner")
        with self.assertRaises(ConversationNotFound):
            self.adapter.select_conversation(conversation["conversation_id"])

    def test_clear_selection_preserves_history_and_next_send_starts_new_conversation(self):
        first = self.adapter.send("Keep this history", request_id="desktop-request-1")
        first_id = first["conversation_id"]
        self.adapter.clear_selection()
        self.assertIsNone(self.adapter.conversation_id)
        second = self.adapter.send("Start a new thread", request_id="desktop-request-2")
        self.assertNotEqual(first_id, second["conversation_id"])
        self.assertEqual(
            self.application.conversations.history(
                owner_id="desktop-owner", conversation_id=first_id,
            )[0]["content"],
            "Keep this history",
        )

    def test_stable_request_id_replays_without_second_model_call(self):
        first = self.adapter.send("Retry safely", request_id="stable-request")
        replay = self.adapter.send("Retry safely", request_id="stable-request")
        self.assertEqual(replay["answer"], first["answer"])
        self.assertTrue(replay["replayed"])
        self.assertEqual(len(self.observed), 1)

    def test_selection_can_change_while_model_generation_is_running(self):
        started = threading.Event()
        release = threading.Event()
        results = []
        errors = []
        original_reply = self.application.conversations.reply

        def blocking_reply(**kwargs):
            started.set()
            if not release.wait(timeout=3):
                raise TimeoutError("test generation was not released")
            return original_reply(**kwargs)

        self.application.conversations.reply = blocking_reply

        def send_turn():
            try:
                results.append(self.adapter.send("Background turn", request_id="background-request"))
            except Exception as exc:
                errors.append(exc)

        worker = threading.Thread(target=send_turn)
        worker.start()
        self.assertTrue(started.wait(timeout=1))
        try:
            # This used to block until model inference completed because send()
            # held the same lock used by clear_selection().
            self.adapter.clear_selection()
            self.assertIsNone(self.adapter.conversation_id)
        finally:
            release.set()
            worker.join(timeout=3)

        self.assertFalse(worker.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["answer"], "Canonical runtime reply")

    def test_memory_is_injected_only_when_context_consent_is_enabled(self):
        class Memory:
            context_enabled = True
            def context_entries(self, *, query, limit):
                return [{"category": "preference", "text": "Prefer concise replies"}]

        adapter = DesktopConversationAdapter(
            self.application, owner_id="desktop-owner", memory=Memory(),
        )
        adapter.send("How should you answer?", request_id="memory-enabled")
        system_message = self.observed[-1][0][0]["content"]
        self.assertIn("Prefer concise replies", system_message)
        self.assertIn("untrusted reference data", system_message)

    def test_memory_retrieval_failure_prevents_model_request(self):
        class Memory:
            context_enabled = True
            def context_entries(self, *, query, limit):
                raise OSError("memory unavailable")

        adapter = DesktopConversationAdapter(
            self.application, owner_id="desktop-owner", memory=Memory(),
        )
        with self.assertRaisesRegex(RuntimeError, "no model request was sent"):
            adapter.send("Do not send without memory", request_id="memory-failure")
        self.assertEqual(self.observed, [])

    def test_memory_is_not_read_without_consent(self):
        class Memory:
            context_enabled = False
            def context_entries(self, **kwargs):
                raise AssertionError("disabled memory must not be read")

        adapter = DesktopConversationAdapter(
            self.application, owner_id="desktop-owner", memory=Memory(),
        )
        adapter.send("No memory", request_id="memory-disabled")
        self.assertNotIn("user-saved memory notes", self.observed[-1][0][0]["content"])

    def test_closed_runtime_rejects_desktop_operations(self):
        self.application.close()
        with self.assertRaisesRegex(RuntimeError, "runtime is closed"):
            self.adapter.send("Do not run")

    def test_invalid_owner_and_message_are_rejected(self):
        with self.assertRaises(ValueError):
            DesktopConversationAdapter(self.application, owner_id="  ")
        with self.assertRaises(ValueError):
            self.adapter.send("  ")


if __name__ == "__main__":
    unittest.main()
