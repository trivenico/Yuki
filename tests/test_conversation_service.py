import json
import tempfile
import unittest
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from core.agent_execution_loop import AgentExecutionLoop
from core.conversation_service import ConversationService
from core.conversation_store import ConversationConflict, ConversationNotFound, ConversationStore
from core.conversation_memory_store import ConversationMemoryStore
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

    def test_closed_service_rejects_new_operations(self):
        self.service.close()
        self.service.close()
        with self.assertRaisesRegex(RuntimeError, "service is closed"):
            self.service.create(owner_id="owner-a")
        with self.assertRaisesRegex(RuntimeError, "service is closed"):
            self.service.list(owner_id="owner-a")
        with self.assertRaisesRegex(RuntimeError, "service is closed"):
            self.service.history(owner_id="owner-a", conversation_id=self.conversation["conversation_id"])
        with self.assertRaisesRegex(RuntimeError, "service is closed"):
            self.send()
        self.assertEqual(self.observed, [])

    def test_authenticated_owner_is_propagated_to_agent_execution(self):
        captured = []
        original_run = self.loop.run

        def recording_run(*args, **kwargs):
            captured.append(kwargs.get("principal_id"))
            return original_run(*args, **kwargs)

        self.loop.run = recording_run
        result = self.send(owner="owner-a")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(captured, ["owner-a"])

    def test_host_approval_callbacks_are_forwarded_with_authenticated_principal(self):
        action_factory = lambda principal, name, arguments: None
        confirmation = lambda call: False
        approval_factory = lambda principal, context, call: None
        pending_factory = lambda context, arguments, principal: "proposal-id"
        service = ConversationService(
            self.store, self.loop,
            action_context_factory=action_factory,
            confirmation_check=confirmation,
            approval_id_factory=approval_factory,
            pending_proposal_factory=pending_factory,
        )
        captured = {}
        original_run = self.loop.run

        def recording_run(*args, **kwargs):
            captured.update(kwargs)
            return original_run(*args, **kwargs)

        self.loop.run = recording_run
        result = service.reply(
            owner_id="owner-a", conversation_id=self.conversation["conversation_id"],
            user_text="Hello", request_id="approval-hooks", expected_version=0, tools=[],
        )
        self.assertEqual(result["status"], "completed")
        self.assertEqual(captured["principal_id"], "owner-a")
        self.assertIs(captured["action_context_factory"], action_factory)
        self.assertIs(captured["confirmation_check"], confirmation)
        self.assertIs(captured["approval_id_factory"], approval_factory)
        self.assertIs(captured["pending_proposal_factory"], pending_factory)

    def test_invalid_host_approval_callbacks_are_rejected(self):
        for name in ("action_context_factory", "confirmation_check", "approval_id_factory"):
            with self.subTest(callback=name), self.assertRaisesRegex(TypeError, name):
                ConversationService(self.store, self.loop, **{name: object()})

    def test_reply_persists_user_and_assistant_and_returns_version(self):
        result = self.send()
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["answer"], "Yuki's answer")
        self.assertEqual(result["version"], 2)
        self.assertEqual(result["memory_references"], [])
        self.assertFalse(result["replayed"])
        history = self.service.history(
            owner_id="owner-a", conversation_id=self.conversation["conversation_id"]
        )
        self.assertEqual([item["role"] for item in history], ["user", "assistant"])

    def test_relevant_saved_memory_is_injected_as_untrusted_reference_context(self):
        memory_store = ConversationMemoryStore(Path(self.tempdir.name) / "memories.sqlite3")
        saved = memory_store.remember(
            owner_id="owner-a", category="preference",
            content="I prefer concise answers with practical examples.",
        )
        service = ConversationService(self.store, self.loop, memory_store=memory_store)
        result = service.reply(
            owner_id="owner-a", conversation_id=self.conversation["conversation_id"],
            user_text="I prefer concise answers, please", request_id="memory-context",
            expected_version=0, tools=[],
        )
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["memory_references"], [{
            "memory_id": saved["memory_id"], "category": "preference",
            "score": memory_store.search(
                owner_id="owner-a", query="I prefer concise answers, please", limit=5,
            )[0]["score"],
        }])
        self.assertNotIn("I prefer concise answers with practical examples", json.dumps(result))
        system_message = next(item for item in self.observed[-1] if item["role"] == "system")
        self.assertIn("Relevant saved context", system_message["content"])
        self.assertIn("untrusted user memory", system_message["content"])
        self.assertIn("I prefer concise answers", system_message["content"])

    def test_saved_memory_is_never_retrieved_across_owners(self):
        memory_store = ConversationMemoryStore(Path(self.tempdir.name) / "memories-isolation.sqlite3")
        memory_store.remember(
            owner_id="owner-a", category="note",
            content="Private phrase: cobalt mountain notebook.",
        )
        other_conversation = self.store.create_conversation(owner_id="owner-b")
        service = ConversationService(self.store, self.loop, memory_store=memory_store)
        result = service.reply(
            owner_id="owner-b", conversation_id=other_conversation["conversation_id"],
            user_text="Tell me about cobalt mountain notebook",
            request_id="memory-owner-isolation", expected_version=0, tools=[],
        )
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["memory_references"], [])
        system_message = next(item for item in self.observed[-1] if item["role"] == "system")
        self.assertNotIn("Private phrase", system_message["content"])
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

    def test_owner_specific_agent_loop_is_cached_and_closed_on_service_shutdown(self):
        selected = []
        closed = []
        def factory(owner_id):
            selected.append(owner_id)
            loop = AgentExecutionLoop(
                lambda **kwargs: {"text": "owner answer", "tool_calls": {"tool_calls": []}},
                StructuredToolProtocol(GuardedToolRuntime({}, AllowNothing())),
            )
            loop.close = lambda: closed.append(owner_id)
            return loop

        service = ConversationService(self.store, self.loop, agent_loop_for_owner=factory)
        result = service.reply(
            owner_id="owner-a", conversation_id=self.conversation["conversation_id"],
            user_text="Owner-specific execution", request_id="owner-loop-request",
            expected_version=0, tools=[],
        )
        self.assertEqual(result["status"], "completed")
        followup = service.reply(
            owner_id="owner-a", conversation_id=self.conversation["conversation_id"],
            user_text="Follow-up", request_id="owner-loop-followup",
            expected_version=2, tools=[],
        )
        self.assertEqual(followup["status"], "completed")
        self.assertEqual(selected, ["owner-a"])
        service.close()
        service.close()
        self.assertEqual(closed, ["owner-a"])

    def test_owner_factory_cannot_share_default_or_another_owners_loop(self):
        service = ConversationService(
            self.store, self.loop, agent_loop_for_owner=lambda owner_id: self.loop,
        )
        result = service.reply(
            owner_id="owner-a", conversation_id=self.conversation["conversation_id"],
            user_text="Invalid shared loop", request_id="invalid-shared-loop",
            expected_version=0, tools=[],
        )
        self.assertEqual(result["status"], "failed")
        service.close()
    def test_owner_specific_agent_loop_factory_must_return_agent_loop(self):
        service = ConversationService(
            self.store, self.loop, agent_loop_for_owner=lambda owner_id: object(),
        )
        result = service.reply(
            owner_id="owner-a", conversation_id=self.conversation["conversation_id"],
            user_text="Invalid owner loop", request_id="invalid-owner-loop",
            expected_version=0, tools=[],
        )
        self.assertEqual(result["status"], "failed")
        self.assertIn("AI service or a guarded operation failed", result["answer"])

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
    def test_shutdown_attempts_every_cached_owner_loop_if_one_close_fails(self):
        created = []
        closed = []
        def factory(owner_id):
            loop = AgentExecutionLoop(
                lambda **kwargs: {"text": owner_id, "tool_calls": {"tool_calls": []}},
                StructuredToolProtocol(GuardedToolRuntime({}, AllowNothing())),
            )
            def close():
                closed.append(owner_id)
                if owner_id == "owner-a":
                    raise RuntimeError("owner loop cleanup failed")
            loop.close = close
            created.append(loop)
            return loop

        service = ConversationService(self.store, self.loop, agent_loop_for_owner=factory)
        for owner_id, request_id in (("owner-a", "owner-a-request"), ("owner-b", "owner-b-request")):
            conversation = self.store.create_conversation(owner_id=owner_id)
            result = service.reply(
                owner_id=owner_id, conversation_id=conversation["conversation_id"],
                user_text="Hello", request_id=request_id, expected_version=0, tools=[],
            )
            self.assertEqual(result["status"], "completed")
        with self.assertRaisesRegex(RuntimeError, "owner loop cleanup failed"):
            service.close()
        with self.assertRaisesRegex(RuntimeError, "owner loop cleanup failed"):
            service.close()
        self.assertEqual(closed, ["owner-a", "owner-b"])


    def test_independent_conversations_generate_concurrently(self):
        rendezvous = threading.Barrier(2, timeout=3)
        calls = []

        def concurrent_model(*, messages, tools, tool_choice):
            calls.append(messages[-1]["content"])
            rendezvous.wait()
            return {"text": "parallel answer", "tool_calls": {"tool_calls": []}}

        loop = AgentExecutionLoop(
            concurrent_model,
            StructuredToolProtocol(GuardedToolRuntime({}, AllowNothing())),
        )
        service = ConversationService(self.store, loop)
        other = service.create(owner_id="owner-a")
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(
                service.reply, owner_id="owner-a",
                conversation_id=self.conversation["conversation_id"],
                user_text="first independent request", request_id="parallel-1",
                expected_version=0, tools=[],
            )
            second = pool.submit(
                service.reply, owner_id="owner-a",
                conversation_id=other["conversation_id"],
                user_text="second independent request", request_id="parallel-2",
                expected_version=0, tools=[],
            )
            self.assertEqual(first.result(timeout=5)["status"], "completed")
            self.assertEqual(second.result(timeout=5)["status"], "completed")
        self.assertCountEqual(
            calls, ["first independent request", "second independent request"]
        )
        self.assertEqual(service._conversation_locks, {})
        self.assertEqual(service._conversation_lock_users, {})

    def test_same_conversation_requests_do_not_enter_model_concurrently(self):
        entered = threading.Event()
        release = threading.Event()
        second_started = threading.Event()
        calls = []

        def serialized_model(*, messages, tools, tool_choice):
            calls.append(messages[-1]["content"])
            entered.set()
            if not release.wait(timeout=5):
                raise TimeoutError("test did not release model")
            return {"text": "serialized answer", "tool_calls": {"tool_calls": []}}

        loop = AgentExecutionLoop(
            serialized_model,
            StructuredToolProtocol(GuardedToolRuntime({}, AllowNothing())),
        )
        service = ConversationService(self.store, loop)

        def second_request():
            second_started.set()
            return service.reply(
                owner_id="owner-a",
                conversation_id=self.conversation["conversation_id"],
                user_text="second request", request_id="same-chat-2",
                expected_version=0, tools=[],
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(
                service.reply, owner_id="owner-a",
                conversation_id=self.conversation["conversation_id"],
                user_text="first request", request_id="same-chat-1",
                expected_version=0, tools=[],
            )
            self.assertTrue(entered.wait(timeout=3))
            second = pool.submit(second_request)
            self.assertTrue(second_started.wait(timeout=3))
            self.assertEqual(calls, ["first request"])
            release.set()
            self.assertEqual(first.result(timeout=5)["status"], "completed")
            with self.assertRaises(ConversationConflict):
                second.result(timeout=5)
        self.assertEqual(calls, ["first request"])
        self.assertEqual(service._conversation_locks, {})

    def test_close_waits_for_active_generation_before_returning(self):
        entered = threading.Event()
        release = threading.Event()
        close_finished = threading.Event()

        def blocking_model(*, messages, tools, tool_choice):
            entered.set()
            if not release.wait(timeout=5):
                raise TimeoutError("test did not release model")
            return {"text": "finished safely", "tool_calls": {"tool_calls": []}}

        loop = AgentExecutionLoop(
            blocking_model,
            StructuredToolProtocol(GuardedToolRuntime({}, AllowNothing())),
        )
        service = ConversationService(self.store, loop)
        with ThreadPoolExecutor(max_workers=2) as pool:
            request = pool.submit(
                service.reply, owner_id="owner-a",
                conversation_id=self.conversation["conversation_id"],
                user_text="hold open", request_id="close-race", expected_version=0,
                tools=[],
            )
            self.assertTrue(entered.wait(timeout=3))
            closing = pool.submit(lambda: (service.close(), close_finished.set()))
            self.assertFalse(close_finished.wait(timeout=0.1))
            release.set()
            self.assertEqual(request.result(timeout=5)["status"], "completed")
            closing.result(timeout=5)
        self.assertTrue(close_finished.is_set())
        with self.assertRaisesRegex(RuntimeError, "service is closed"):
            service.create(owner_id="owner-a")


    def test_request_capacity_rejects_overload_and_recovers_after_completion(self):
        entered = threading.Event()
        release = threading.Event()

        def blocking_model(*, messages, tools, tool_choice):
            entered.set()
            if not release.wait(timeout=5):
                raise TimeoutError("test did not release model")
            return {"text": "capacity recovered", "tool_calls": {"tool_calls": []}}

        loop = AgentExecutionLoop(
            blocking_model,
            StructuredToolProtocol(GuardedToolRuntime({}, AllowNothing())),
        )
        service = ConversationService(self.store, loop, max_active_requests=1)
        first_conversation = service.create(owner_id="owner-a")
        second_conversation = service.create(owner_id="owner-a")
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(
                service.reply, owner_id="owner-a",
                conversation_id=first_conversation["conversation_id"],
                user_text="occupy capacity", request_id="capacity-1",
                expected_version=0, tools=[],
            )
            self.assertTrue(entered.wait(timeout=3))
            with self.assertRaisesRegex(RuntimeError, "at capacity"):
                service.reply(
                    owner_id="owner-a",
                    conversation_id=second_conversation["conversation_id"],
                    user_text="over capacity", request_id="capacity-2",
                    expected_version=0, tools=[],
                )
            self.assertEqual(service._active_requests, 1)
            release.set()
            self.assertEqual(first.result(timeout=5)["status"], "completed")
        self.assertEqual(service._active_requests, 0)
        recovered = service.reply(
            owner_id="owner-a",
            conversation_id=second_conversation["conversation_id"],
            user_text="capacity is free", request_id="capacity-3",
            expected_version=0, tools=[],
        )
        self.assertEqual(recovered["status"], "completed")
        self.assertEqual(service._active_requests, 0)
        self.assertEqual(service._conversation_locks, {})

    def test_request_capacity_must_be_a_positive_integer(self):
        for value in (0, -1, True, 1.5):
            with self.subTest(value=value), self.assertRaises(ValueError):
                ConversationService(self.store, self.loop, max_active_requests=value)


    def test_owner_loop_cleanup_runs_without_holding_service_lock(self):
        service_holder = []
        lock_acquired = threading.Event()
        def factory(owner_id):
            loop = AgentExecutionLoop(
                lambda **kwargs: {"text": owner_id, "tool_calls": {"tool_calls": []}},
                StructuredToolProtocol(GuardedToolRuntime({}, AllowNothing())),
            )
            def close():
                service = service_holder[0]
                def acquire_service_lock():
                    with service._lock:
                        lock_acquired.set()
                worker = threading.Thread(target=acquire_service_lock)
                worker.start()
                worker.join(timeout=2)
                if worker.is_alive():
                    raise RuntimeError("service lock held during owner loop cleanup")
            loop.close = close
            return loop

        service = ConversationService(self.store, self.loop, agent_loop_for_owner=factory)
        service_holder.append(service)
        conversation = self.store.create_conversation(owner_id="owner-a")
        result = service.reply(
            owner_id="owner-a", conversation_id=conversation["conversation_id"],
            user_text="Hello", request_id="lock-release", expected_version=0, tools=[],
        )
        self.assertEqual(result["status"], "completed")
        service.close()
        self.assertTrue(lock_acquired.is_set())

    def test_concurrent_close_waits_for_first_cleanup_to_finish(self):
        entered = threading.Event()
        release = threading.Event()
        service = ConversationService(self.store, self.loop)
        def close_loop():
            entered.set()
            if not release.wait(timeout=5):
                raise TimeoutError("test did not release shutdown")
        loop = AgentExecutionLoop(
            lambda **kwargs: {"text": "owner", "tool_calls": {"tool_calls": []}},
            StructuredToolProtocol(GuardedToolRuntime({}, AllowNothing())),
        )
        loop.close = close_loop
        service._owner_agent_loops["owner-a"] = loop
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(service.close)
            self.assertTrue(entered.wait(timeout=3))
            second = pool.submit(service.close)
            self.assertFalse(second.done())
            release.set()
            first.result(timeout=5)
            second.result(timeout=5)
        self.assertTrue(service._close_complete)

if __name__ == "__main__":
    unittest.main()
