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

    def test_stale_version_fails_before_model_call(self):
        self.send()
        with self.assertRaises(ConversationConflict):
            self.send(text="Stale", request="request-2", version=0)
        self.assertEqual(len(self.observed), 1)

    def test_owner_isolation_is_enforced_by_service(self):
        with self.assertRaises(ConversationNotFound):
            self.send(owner="owner-b")
        self.assertEqual(self.observed, [])

    def test_authenticated_session_context_reaches_owner_tool_policy(self):
        observed = []

        class ContextPolicy:
            def authorize_tool(self, name):
                return False

            def authorize_tool_context(self, name, context):
                observed.append((name, dict(context)))
                return context["actor"] == "owner-a"

        def factory(owner_id):
            runtime = GuardedToolRuntime(
                {"owner.read": lambda raw: "private-data"},
                ContextPolicy(),
            )
            responses = iter([
                {"text": "", "tool_calls": {"tool_calls": [
                    {"id": "read_1", "name": "owner.read", "arguments": {}}
                ]}},
                {"text": "Done", "tool_calls": {"tool_calls": []}},
            ])
            return AgentExecutionLoop(lambda **kwargs: next(responses), StructuredToolProtocol(runtime))

        service = ConversationService(self.store, self.loop, agent_loop_for_owner=factory)
        definition = [{
            "type": "function",
            "function": {"name": "owner.read", "parameters": {"type": "object", "properties": {}}},
        }]
        result = service.reply(
            owner_id="owner-a",
            conversation_id=self.conversation["conversation_id"],
            user_text="Read my data",
            request_id="authenticated-context-request",
            expected_version=0,
            tools=definition,
            session_id="sha256-session-digest",
        )
        self.assertEqual(result["status"], "completed")
        self.assertEqual(observed, [("owner.read", {
            "actor": "owner-a",
            "session_id": "sha256-session-digest",
            "conversation_id": self.conversation["conversation_id"],
            "request_id": "authenticated-context-request",
        })])

    def test_owner_factory_routes_authenticated_owners_to_distinct_loops(self):
        loops = {}
        invoked = []

        def factory(owner_id):
            invoked.append(owner_id)
            protocol = StructuredToolProtocol(GuardedToolRuntime({}, AllowNothing()))
            loop = AgentExecutionLoop(
                lambda **kwargs: {"text": "answer:" + owner_id, "tool_calls": {"tool_calls": []}},
                protocol,
            )
            loops[owner_id] = loop
            return loop

        service = ConversationService(self.store, self.loop, agent_loop_for_owner=factory)
        first = service.reply(
            owner_id="owner-a", conversation_id=self.conversation["conversation_id"],
            user_text="first", request_id="owner-a-request", expected_version=0, tools=[],
        )
        other = service.create(owner_id="owner-b")
        second = service.reply(
            owner_id="owner-b", conversation_id=other["conversation_id"],
            user_text="second", request_id="owner-b-request", expected_version=0, tools=[],
        )
        self.assertEqual(first["answer"], "answer:owner-a")
        self.assertEqual(second["answer"], "answer:owner-b")
        self.assertIsNot(loops["owner-a"], loops["owner-b"])
        self.assertEqual(invoked, ["owner-a", "owner-b"])


    def test_owner_tool_catalog_must_match_owner_guarded_handlers(self):
        invoked = []
        model_calls = []

        class AllowRegistered:
            def authorize_tool(self, name):
                return name == "owner_safe"

        def factory(owner_id):
            runtime = GuardedToolRuntime(
                {"owner_safe": lambda argument: invoked.append(owner_id) or "ok"},
                AllowRegistered(),
                audit=lambda name, outcome: invoked.append((owner_id, name, outcome)),
            )
            return AgentExecutionLoop(
                lambda **kwargs: model_calls.append(owner_id) or {
                    "text": "answer", "tool_calls": {"tool_calls": []}
                },
                StructuredToolProtocol(runtime),
            )

        service = ConversationService(self.store, self.loop, agent_loop_for_owner=factory)
        result = service.reply(
            owner_id="owner-a", conversation_id=self.conversation["conversation_id"],
            user_text="use tools", request_id="mismatched-tool-catalog",
            expected_version=0,
            tools=[{"type": "function", "function": {"name": "owner_other", "parameters": {"type": "object"}}}],
        )
        self.assertEqual(result["status"], "failed")
        self.assertEqual(model_calls, [])
        self.assertEqual(invoked, [])

    def test_owner_tool_catalog_accepts_exact_registered_handler_names(self):
        model_calls = []
        def factory(owner_id):
            runtime = GuardedToolRuntime({"owner_safe": lambda argument: "ok"}, AllowNothing())
            return AgentExecutionLoop(
                lambda **kwargs: model_calls.append(owner_id) or {
                    "text": "answer", "tool_calls": {"tool_calls": []}
                },
                StructuredToolProtocol(runtime),
            )

        service = ConversationService(self.store, self.loop, agent_loop_for_owner=factory)
        result = service.reply(
            owner_id="owner-a", conversation_id=self.conversation["conversation_id"],
            user_text="hello", request_id="matching-tool-catalog", expected_version=0,
            tools=[{"type": "function", "function": {"name": "owner_safe", "parameters": {"type": "object"}}}],
        )
        self.assertEqual(result["status"], "completed")
        self.assertEqual(model_calls, ["owner-a"])

    def test_owner_factory_rejects_cross_owner_loop_reuse(self):
        protocol = StructuredToolProtocol(GuardedToolRuntime({}, AllowNothing()))
        shared = AgentExecutionLoop(
            lambda **kwargs: {"text": "shared", "tool_calls": {"tool_calls": []}},
            protocol,
        )
        service = ConversationService(self.store, self.loop, agent_loop_for_owner=lambda owner: shared)
        first = service.reply(
            owner_id="owner-a", conversation_id=self.conversation["conversation_id"],
            user_text="first", request_id="shared-owner-a", expected_version=0, tools=[],
        )
        other = service.create(owner_id="owner-b")
        second = service.reply(
            owner_id="owner-b", conversation_id=other["conversation_id"],
            user_text="second", request_id="shared-owner-b", expected_version=0, tools=[],
        )
        self.assertEqual(first["answer"], "shared")
        self.assertEqual(second["status"], "failed")
        self.assertNotIn("shared", second["answer"])

    def test_independent_owner_conversations_execute_concurrently(self):
        import threading
        barrier = threading.Barrier(2)
        results = []
        errors = []

        def factory(owner_id):
            def model(**kwargs):
                try:
                    barrier.wait(timeout=3)
                except threading.BrokenBarrierError:
                    raise AssertionError("independent owners were serialized")
                return {"text": owner_id, "tool_calls": {"tool_calls": []}}
            return AgentExecutionLoop(model, StructuredToolProtocol(
                GuardedToolRuntime({}, AllowNothing())
            ))

        service = ConversationService(self.store, self.loop, agent_loop_for_owner=factory)
        other = service.create(owner_id="owner-b")
        requests = [
            ("owner-a", self.conversation["conversation_id"], "parallel-a"),
            ("owner-b", other["conversation_id"], "parallel-b"),
        ]

        def run(owner, conversation_id, request_id):
            try:
                results.append(service.reply(
                    owner_id=owner, conversation_id=conversation_id,
                    user_text="parallel", request_id=request_id,
                    expected_version=0, tools=[],
                ))
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=run, args=item) for item in requests]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)
        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(errors, [])
        self.assertEqual({item["answer"] for item in results}, {"owner-a", "owner-b"})

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
