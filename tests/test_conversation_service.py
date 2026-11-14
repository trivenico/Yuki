import concurrent.futures
import tempfile
import threading
import unittest
from pathlib import Path

from core.agent_execution_loop import AgentExecutionLoop
from core.conversation_service import ConversationService
from core.conversation_store import ConversationConflict, ConversationNotFound, ConversationStore
from core.guarded_tool_runtime import GuardedToolRuntime
from core.structured_tool_protocol import StructuredToolProtocol


class AllowNothing:
    def authorize_tool(self, name):
        return False


class ConversationServiceTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.store = ConversationStore(Path(self.tempdir.name) / "chat.sqlite3")
        self.observed = []

        def model(*, messages, tools, tool_choice):
            self.observed.append(messages)
            return {"text": "Yuki's answer", "tool_calls": {"tool_calls": []}}

        protocol = StructuredToolProtocol(GuardedToolRuntime({}, AllowNothing()))
        self.loop = AgentExecutionLoop(model, protocol)
        self.service = ConversationService(self.store, self.loop)
        self.conversation = self.service.create(owner_id="owner-a")

    def tearDown(self):
        self.tempdir.cleanup()

    def send(self, *, owner="owner-a", text="Hello", request="request-1", version=0):
        return self.service.reply(
            owner_id=owner,
            conversation_id=self.conversation["conversation_id"],
            user_text=text,
            request_id=request,
            expected_version=version,
            tools=[],
        )

    def test_reply_persists_user_and_assistant_and_returns_version(self):
        result = self.send()
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["answer"], "Yuki's answer")
        self.assertEqual(result["version"], 2)
        self.assertFalse(result["replayed"])
        history = self.service.history(
            owner_id="owner-a", conversation_id=self.conversation["conversation_id"]
        )
        self.assertEqual([item["role"] for item in history], ["user", "assistant"])

    def test_next_turn_receives_prior_persisted_transcript(self):
        self.send()
        second = self.send(text="Follow up", request="request-2", version=2)
        self.assertEqual(second["version"], 4)
        self.assertEqual(
            self.observed[1][1:],
            [
                {"role": "user", "content": "Hello"},
                {"role": "assistant", "content": "Yuki's answer"},
                {"role": "user", "content": "Follow up"},
            ],
        )

    def test_idempotent_retry_replays_answer_without_second_model_call(self):
        first = self.send()
        replay = self.send()
        self.assertEqual(replay["answer"], first["answer"])
        self.assertTrue(replay["replayed"])
        self.assertEqual(len(self.observed), 1)

    def test_same_conversation_rejects_overlapping_generation(self):
        started = threading.Event()
        release = threading.Event()
        calls = []

        def blocking_model(**kwargs):
            calls.append(kwargs)
            started.set()
            if not release.wait(timeout=5):
                raise TimeoutError("test model release timed out")
            return {"text": "first generation completed", "tool_calls": {"tool_calls": []}}

        protocol = StructuredToolProtocol(GuardedToolRuntime({}, AllowNothing()))
        service = ConversationService(self.store, AgentExecutionLoop(blocking_model, protocol))
        first_args = {
            "owner_id": "owner-a",
            "conversation_id": self.conversation["conversation_id"],
            "user_text": "first request", "request_id": "overlap-first",
            "expected_version": 0, "tools": [],
        }
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                first = pool.submit(service.reply, **first_args)
                self.assertTrue(started.wait(timeout=5), "first model invocation did not start")
                with self.assertRaisesRegex(ConversationConflict, "another generation is already active"):
                    service.reply(
                        owner_id="owner-a",
                        conversation_id=self.conversation["conversation_id"],
                        user_text="second request", request_id="overlap-second",
                        expected_version=0, tools=[],
                    )
                self.assertEqual(len(calls), 1)
                release.set()
                result = first.result(timeout=5)
            self.assertEqual(result["status"], "completed")
            self.assertEqual(result["answer"], "first generation completed")
            self.assertEqual(len(calls), 1)
        finally:
            release.set()

    def test_stale_version_fails_before_model_call(self):
        self.send()
        with self.assertRaises(ConversationConflict):
            self.send(text="Stale", request="request-2", version=0)
        self.assertEqual(len(self.observed), 1)

    def test_owner_isolation_is_enforced_by_service(self):
        with self.assertRaises(ConversationNotFound):
            self.send(owner="owner-b")
        self.assertEqual(self.observed, [])

    def test_model_failure_is_sanitized_and_persisted(self):
        def failing_model(**kwargs):
            raise RuntimeError("secret provider response and credentials")
        protocol = StructuredToolProtocol(GuardedToolRuntime({}, AllowNothing()))
        failing = ConversationService(
            self.store, AgentExecutionLoop(failing_model, protocol)
        )
        result = failing.reply(
            owner_id="owner-a", conversation_id=self.conversation["conversation_id"],
            user_text="Try this", request_id="failed-request", expected_version=0, tools=[],
        )
        self.assertEqual(result["status"], "failed")
        self.assertNotIn("secret", result["answer"])
        self.assertEqual(result["version"], 2)
        replay = failing.reply(
            owner_id="owner-a", conversation_id=self.conversation["conversation_id"],
            user_text="Try this", request_id="failed-request", expected_version=0, tools=[],
        )
        self.assertTrue(replay["replayed"])
        self.assertEqual(replay["answer"], result["answer"])



    def test_cancelled_generation_persists_explicit_stopped_status(self):
        result = self.service.reply(
            owner_id="owner-a",
            conversation_id=self.conversation["conversation_id"],
            user_text="Stop me",
            request_id="cancelled-request",
            expected_version=0,
            tools=[],
            cancellation_requested=lambda: True,
        )
        self.assertEqual(result["status"], "cancelled")
        self.assertEqual(result["answer"], "Generation was stopped at your request.")
        self.assertEqual(result["version"], 2)


    def test_interrupted_generation_is_reconciled_without_replaying_model(self):
        self.store.append_message(
            owner_id="owner-a", conversation_id=self.conversation["conversation_id"],
            role="user", content="Potentially side-effecting task",
            idempotency_key="crashed-request", expected_version=0,
            generation_request_id="crashed-request",
        )
        replay = self.service.reply(
            owner_id="owner-a", conversation_id=self.conversation["conversation_id"],
            user_text="Potentially side-effecting task", request_id="crashed-request",
            expected_version=0, tools=[],
        )
        self.assertTrue(replay["replayed"])
        self.assertIn("interrupted", replay["answer"])
        self.assertEqual(self.observed, [])
        self.assertEqual(self.store.list_interrupted_generations(
            owner_id="owner-a", conversation_id=self.conversation["conversation_id"]
        ), [])
        history = self.service.history(
            owner_id="owner-a", conversation_id=self.conversation["conversation_id"]
        )
        self.assertEqual([item["role"] for item in history], ["user", "assistant"])

    def test_new_turn_recovers_previous_crash_before_calling_model(self):
        self.store.append_message(
            owner_id="owner-a", conversation_id=self.conversation["conversation_id"],
            role="user", content="Previous interrupted task",
            idempotency_key="old-request", expected_version=0,
            generation_request_id="old-request",
        )
        result = self.service.reply(
            owner_id="owner-a", conversation_id=self.conversation["conversation_id"],
            user_text="Continue with a new task", request_id="new-request",
            expected_version=1, tools=[],
        )
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["version"], 4)
        history = self.service.history(
            owner_id="owner-a", conversation_id=self.conversation["conversation_id"]
        )
        self.assertEqual(
            [item["role"] for item in history],
            ["user", "assistant", "user", "assistant"],
        )
        self.assertIn("interrupted", history[1]["content"])
        self.assertEqual(len(self.observed), 1)


    def test_recovery_marks_run_completed_if_assistant_was_already_persisted(self):
        user = self.store.append_message(
            owner_id="owner-a", conversation_id=self.conversation["conversation_id"],
            role="user", content="Already answered", idempotency_key="persisted-request",
            expected_version=0, generation_request_id="persisted-request",
        )
        self.store.append_message(
            owner_id="owner-a", conversation_id=self.conversation["conversation_id"],
            role="assistant", content="Persisted answer",
            idempotency_key="assistant:persisted-request", expected_version=user["version"],
        )
        replay = self.service.reply(
            owner_id="owner-a", conversation_id=self.conversation["conversation_id"],
            user_text="Already answered", request_id="persisted-request",
            expected_version=0, tools=[],
        )
        self.assertTrue(replay["replayed"])
        self.assertEqual(replay["answer"], "Persisted answer")
        self.assertEqual(self.observed, [])
        self.assertEqual(self.store.list_interrupted_generations(
            owner_id="owner-a", conversation_id=self.conversation["conversation_id"]
        ), [])

if __name__ == "__main__":
    unittest.main()
