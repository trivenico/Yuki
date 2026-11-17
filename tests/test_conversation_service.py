import tempfile
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

    def test_owner_scoped_model_loop_factory_is_used_for_authenticated_owner(self):
        selected = []
        owner_messages = []

        def model(*, messages, tools, tool_choice):
            owner_messages.append(messages)
            return {"text": "Owner-specific answer", "tool_calls": {"tool_calls": []}}

        owner_loop = AgentExecutionLoop(
            model, StructuredToolProtocol(GuardedToolRuntime({}, AllowNothing()))
        )
        service = ConversationService(
            self.store, self.loop,
            agent_loop_for_owner=lambda owner_id: selected.append(owner_id) or owner_loop,
        )
        result = service.reply(
            owner_id="owner-a",
            conversation_id=self.conversation["conversation_id"],
            user_text="Use my configured model",
            request_id="owner-model-request",
            expected_version=0,
            tools=[],
        )
        self.assertEqual(selected, ["owner-a"])
        self.assertEqual(result["answer"], "Owner-specific answer")
        self.assertEqual(len(owner_messages), 1)
        self.assertEqual(self.observed, [])

    def test_owner_scoped_model_factory_rejects_invalid_result_before_persisting(self):
        service = ConversationService(
            self.store, self.loop, agent_loop_for_owner=lambda owner_id: object()
        )
        with self.assertRaisesRegex(TypeError, "must return an AgentExecutionLoop"):
            service.reply(
                owner_id="owner-a",
                conversation_id=self.conversation["conversation_id"],
                user_text="Do not persist this",
                request_id="invalid-owner-model-request",
                expected_version=0,
                tools=[],
            )
        self.assertEqual(self.service.history(
            owner_id="owner-a", conversation_id=self.conversation["conversation_id"]
        ), [])

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

    def test_idempotent_retry_does_not_construct_owner_model(self):
        selected = []
        service = ConversationService(
            self.store, self.loop,
            agent_loop_for_owner=lambda owner_id: selected.append(owner_id) or self.loop,
        )
        first = service.reply(
            owner_id="owner-a", conversation_id=self.conversation["conversation_id"],
            user_text="Hello", request_id="factory-replay", expected_version=0, tools=[],
        )
        replay = service.reply(
            owner_id="owner-a", conversation_id=self.conversation["conversation_id"],
            user_text="Hello", request_id="factory-replay", expected_version=0, tools=[],
        )
        self.assertFalse(first["replayed"])
        self.assertTrue(replay["replayed"])
        self.assertEqual(selected, ["owner-a"])

    def test_wrong_owner_does_not_construct_owner_model(self):
        selected = []
        service = ConversationService(
            self.store, self.loop,
            agent_loop_for_owner=lambda owner_id: selected.append(owner_id) or self.loop,
        )
        with self.assertRaises(ConversationNotFound):
            service.reply(
                owner_id="owner-b", conversation_id=self.conversation["conversation_id"],
                user_text="Unauthorized", request_id="wrong-owner", expected_version=0, tools=[],
            )
        self.assertEqual(selected, [])

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


    def test_persistent_conversation_uses_yuki_identity_and_default_role(self):
        self.send()
        system = self.observed[0][0]
        self.assertEqual(system["role"], "system")
        self.assertIn("You are Yuki", system["content"])
        self.assertIn("Current response role: companion", system["content"])
        self.assertIn("grants no tools, permissions", system["content"])

    def test_role_selection_changes_guidance_without_changing_tool_policy(self):
        selected = self.service.set_role("developer")
        self.assertEqual(selected["name"], "developer")
        self.assertEqual(self.service.current_role()["name"], "developer")
        self.send()
        system = self.observed[0][0]["content"]
        self.assertIn("Current response role: developer", system)
        self.assertIn("grants no tools, permissions", system)
        self.assertEqual(self.service.available_roles()[0]["name"], "companion")

    def test_unknown_role_is_rejected_before_provider_call(self):
        with self.assertRaises(ValueError):
            self.service.set_role("administrator")
        self.assertEqual(self.observed, [])

    def test_additional_trusted_system_guidance_is_preserved(self):
        self.service.reply(
            owner_id="owner-a",
            conversation_id=self.conversation["conversation_id"],
            user_text="Hello",
            request_id="system-guidance-request",
            expected_version=0,
            tools=[],
            system_text="Use concise answers for this workspace.",
        )
        system = self.observed[0][0]["content"]
        self.assertIn("You are Yuki", system)
        self.assertIn("Use concise answers for this workspace.", system)

if __name__ == "__main__":
    unittest.main()
