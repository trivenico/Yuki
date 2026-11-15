import concurrent.futures
import multiprocessing
import tempfile
import threading
import unittest
from unittest.mock import patch
from pathlib import Path

from core.agent_execution_loop import AgentExecutionLoop
from core.conversation_service import ConversationService
from core.conversation_recovery import ConversationRecoveryCoordinator
from core.conversation_store import ConversationConflict, ConversationNotFound, ConversationStore
from core.guarded_tool_runtime import GuardedToolRuntime
from core.structured_tool_protocol import StructuredToolProtocol




def _multiprocess_claim_generation_lease(database_path, conversation_id, token, barrier, results):
    store = ConversationStore(database_path)
    try:
        barrier.wait(timeout=10)
        store.acquire_generation_lease(
            owner_id="owner-a", conversation_id=conversation_id,
            lease_token=token, ttl_seconds=60,
        )
    except ConversationConflict:
        results.put(("conflict", token))
    except Exception as exc:
        results.put(("error", repr(exc)))
    else:
        results.put(("acquired", token))
        store.release_generation_lease(
            owner_id="owner-a", conversation_id=conversation_id,
            lease_token=token,
        )


def _multiprocess_mark_uncertain_then_crash(database_path, conversation_id, request_id, connection):
    import os
    import core.conversation_store as conversation_store_module

    conversation_store_module.time.time = lambda: 100.0
    store = ConversationStore(database_path)
    token = "recovery-crash-window-worker"
    store.acquire_generation_lease(
        owner_id="owner-a", conversation_id=conversation_id,
        lease_token=token, ttl_seconds=5,
    )
    marked = store.mark_started_tool_executions_uncertain(
        owner_id="owner-a", conversation_id=conversation_id,
        request_id=request_id, lease_token=token,
    )
    connection.send(("marked-uncertain", marked))
    connection.close()
    os._exit(0)


def _multiprocess_crash_after_generation_lease(database_path, conversation_id, token, connection):
    import os
    import core.conversation_store as conversation_store_module

    conversation_store_module.time.time = lambda: 100.0
    store = ConversationStore(database_path)
    store.acquire_generation_lease(
        owner_id="owner-a", conversation_id=conversation_id,
        lease_token=token, ttl_seconds=5,
    )
    connection.send("lease-acquired")
    connection.close()
    os._exit(0)

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

    def test_recovery_coordinator_returns_structured_outcomes(self):
        conversation_id = self.conversation["conversation_id"]
        self.store.append_message(
            owner_id="owner-a", conversation_id=conversation_id,
            role="user", content="Interrupted request", idempotency_key="coordinator-run",
            expected_version=0, generation_request_id="coordinator-run",
        )
        lease_token = "recovery-coordinator-test-lease"
        self.store.acquire_generation_lease(
            owner_id="owner-a", conversation_id=conversation_id,
            lease_token=lease_token, ttl_seconds=60,
        )
        try:
            summary = ConversationRecoveryCoordinator(self.store).recover(
                owner_id="owner-a", conversation_id=conversation_id,
                lease_token=lease_token,
            )
        finally:
            self.store.release_generation_lease(
                owner_id="owner-a", conversation_id=conversation_id,
                lease_token=lease_token,
            )

        self.assertEqual(summary.recovered_request_ids, ("coordinator-run",))
        self.assertEqual(summary.interrupted_generations, 1)
        self.assertEqual(summary.completed_generations, 0)
        self.assertEqual(summary.orphaned_generations, 0)
        self.assertFalse(summary.batch_limit_reached)
        events = self.service.recovery_events(
            owner_id="owner-a", conversation_id=conversation_id,
        )
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["request_id"], "coordinator-run")
        self.assertEqual(events[0]["outcome"], "interrupted")
        self.assertEqual(events[0]["uncertain_tool_calls"], 0)
        with self.assertRaises(ConversationNotFound):
            self.service.recovery_events(
                owner_id="owner-b", conversation_id=conversation_id,
            )

    def _seed_interrupted_generation(self, request_id, expected_version):
        return self.store.append_message(
            owner_id="owner-a",
            conversation_id=self.conversation["conversation_id"],
            role="user",
            content=f"Interrupted {request_id}",
            idempotency_key=request_id,
            expected_version=expected_version,
            generation_request_id=request_id,
        )

    def _with_recovery_lease(self, callback):
        conversation_id = self.conversation["conversation_id"]
        token = "recovery-failure-test-lease"
        self.store.acquire_generation_lease(
            owner_id="owner-a", conversation_id=conversation_id,
            lease_token=token, ttl_seconds=60,
        )
        try:
            return callback(token)
        finally:
            self.store.release_generation_lease(
                owner_id="owner-a", conversation_id=conversation_id,
                lease_token=token,
            )

    def test_recovery_append_failure_leaves_generation_retryable(self):
        conversation_id = self.conversation["conversation_id"]
        self._seed_interrupted_generation("retryable-recovery", 0)
        coordinator = ConversationRecoveryCoordinator(self.store)
        original_append = self.store.append_message

        def fail_append(**kwargs):
            raise OSError("simulated disk write failure")

        def attempt(token):
            with patch.object(self.store, "append_message", side_effect=fail_append):
                with self.assertRaisesRegex(OSError, "simulated disk write failure"):
                    coordinator.recover(
                        owner_id="owner-a", conversation_id=conversation_id,
                        lease_token=token,
                    )
            pending = self.store.list_interrupted_generations(
                owner_id="owner-a", conversation_id=conversation_id,
            )
            self.assertEqual([item["request_id"] for item in pending], ["retryable-recovery"])
            self.assertEqual(self.store.list_recovery_events(
                owner_id="owner-a", conversation_id=conversation_id,
            ), [])
            return coordinator.recover(
                owner_id="owner-a", conversation_id=conversation_id,
                lease_token=token,
            )

        summary = self._with_recovery_lease(attempt)
        self.assertEqual(summary.interrupted_generations, 1)
        self.assertEqual(len(self.store.list_recovery_events(
            owner_id="owner-a", conversation_id=conversation_id,
        )), 1)

    def test_lease_replacement_after_recovery_message_fails_closed_and_retries(self):
        conversation_id = self.conversation["conversation_id"]
        self._seed_interrupted_generation("lease-race-recovery", 0)
        coordinator = ConversationRecoveryCoordinator(self.store)
        original_finalize = self.store.finish_generation_with_recovery_event

        def lose_lease_then_finalize(**kwargs):
            with self.store._connect() as db:
                db.execute(
                    """UPDATE conversation_generation_leases
                       SET lease_token=?, expires_at=?
                       WHERE owner_id=? AND conversation_id=?""",
                    ("replacement-worker-token", 9999999999,
                     "owner-a", conversation_id),
                )
            return original_finalize(**kwargs)

        token = "recovery-race-original-token"
        self.store.acquire_generation_lease(
            owner_id="owner-a", conversation_id=conversation_id,
            lease_token=token, ttl_seconds=60,
        )
        try:
            with patch.object(
                self.store, "finish_generation_with_recovery_event",
                side_effect=lose_lease_then_finalize,
            ):
                with self.assertRaisesRegex(ConversationConflict, "lease expired"):
                    coordinator.recover(
                        owner_id="owner-a", conversation_id=conversation_id,
                        lease_token=token,
                    )
            self.assertTrue(self.store.has_message(
                owner_id="owner-a", conversation_id=conversation_id,
                idempotency_key="assistant:lease-race-recovery",
            ))
            self.assertEqual(self.store.list_recovery_events(
                owner_id="owner-a", conversation_id=conversation_id,
            ), [])
            self.assertEqual(len(self.store.list_interrupted_generations(
                owner_id="owner-a", conversation_id=conversation_id,
            )), 1)
        finally:
            self.store.release_generation_lease(
                owner_id="owner-a", conversation_id=conversation_id,
                lease_token="replacement-worker-token",
            )

        retry_token = "recovery-race-retry-token"
        self.store.acquire_generation_lease(
            owner_id="owner-a", conversation_id=conversation_id,
            lease_token=retry_token, ttl_seconds=60,
        )
        try:
            summary = coordinator.recover(
                owner_id="owner-a", conversation_id=conversation_id,
                lease_token=retry_token,
            )
        finally:
            self.store.release_generation_lease(
                owner_id="owner-a", conversation_id=conversation_id,
                lease_token=retry_token,
            )
        self.assertEqual(summary.completed_generations, 1)
        self.assertEqual(self.store.list_recovery_events(
            owner_id="owner-a", conversation_id=conversation_id,
        )[0]["outcome"], "completed")
        messages = self.store.list_messages(
            owner_id="owner-a", conversation_id=conversation_id, limit=100,
        )
        interruption_messages = [
            message for message in messages
            if message["role"] == "assistant"
            and message["content"] == coordinator.INTERRUPTION_MESSAGE
        ]
        self.assertEqual(len(interruption_messages), 1)
        repeated = coordinator.recover(
            owner_id="owner-a", conversation_id=conversation_id,
            lease_token=retry_token,
        )
        self.assertEqual(repeated.recovered_request_ids, ())
        messages_after_retry = self.store.list_messages(
            owner_id="owner-a", conversation_id=conversation_id, limit=100,
        )
        self.assertEqual(
            sum(1 for message in messages_after_retry
                if message["role"] == "assistant"
                and message["content"] == coordinator.INTERRUPTION_MESSAGE),
            1,
        )

    def test_recovery_audit_insert_failure_rolls_back_generation_finalization(self):
        conversation_id = self.conversation["conversation_id"]
        self._seed_interrupted_generation("audit-rollback", 0)
        with self.store._connect() as db:
            db.execute("""CREATE TRIGGER fail_recovery_audit
                          BEFORE INSERT ON conversation_recovery_events
                          BEGIN SELECT RAISE(ABORT, 'simulated audit outage'); END""")

        def attempt(token):
            with self.assertRaisesRegex(Exception, "simulated audit outage"):
                ConversationRecoveryCoordinator(self.store).recover(
                    owner_id="owner-a", conversation_id=conversation_id,
                    lease_token=token,
                )
            pending = self.store.list_interrupted_generations(
                owner_id="owner-a", conversation_id=conversation_id,
            )
            self.assertEqual([item["request_id"] for item in pending], ["audit-rollback"])
            self.assertEqual(self.store.list_recovery_events(
                owner_id="owner-a", conversation_id=conversation_id,
            ), [])
            with self.store._connect() as db:
                db.execute("DROP TRIGGER fail_recovery_audit")
            return ConversationRecoveryCoordinator(self.store).recover(
                owner_id="owner-a", conversation_id=conversation_id,
                lease_token=token,
            )

        summary = self._with_recovery_lease(attempt)
        self.assertEqual(summary.interrupted_generations, 1)
        self.assertEqual(len(self.store.list_recovery_events(
            owner_id="owner-a", conversation_id=conversation_id,
        )), 1)

    def test_saturated_recovery_blocks_new_model_work_until_backlog_is_drained(self):
        conversation_id = self.conversation["conversation_id"]
        version = 0
        for index in range(ConversationRecoveryCoordinator.BATCH_LIMIT + 1):
            result = self._seed_interrupted_generation(f"service-backlog-{index}", version)
            version = result["version"]

        with self.assertRaisesRegex(ConversationConflict, "safety limit"):
            self.service.reply(
                owner_id="owner-a", conversation_id=conversation_id,
                user_text="Do not run until recovery is clear",
                request_id="blocked-by-recovery-backlog",
                expected_version=version, tools=[],
            )
        self.assertEqual(self.observed, [])
        self.assertEqual(len(self.store.list_interrupted_generations(
            owner_id="owner-a", conversation_id=conversation_id,
        )), 1)

        current = self.store.get_conversation(
            owner_id="owner-a", conversation_id=conversation_id,
        )
        result = self.service.reply(
            owner_id="owner-a", conversation_id=conversation_id,
            user_text="Proceed after recovery",
            request_id="after-recovery-backlog",
            expected_version=current["version"], tools=[],
        )
        self.assertEqual(result["status"], "completed")
        self.assertEqual(len(self.observed), 1)
        self.assertEqual(self.store.list_interrupted_generations(
            owner_id="owner-a", conversation_id=conversation_id,
        ), [])

    def test_recovery_finds_original_user_message_beyond_recent_history_window(self):
        conversation_id = self.conversation["conversation_id"]
        first = self._seed_interrupted_generation("old-recovery-turn", 0)
        version = first["version"]
        for index in range(200):
            result = self.store.append_message(
                owner_id="owner-a", conversation_id=conversation_id,
                role="assistant", content=f"Later persisted message {index}",
                idempotency_key=f"later-message-{index}", expected_version=version,
            )
            version = result["version"]

        summary = self._with_recovery_lease(lambda token:
            ConversationRecoveryCoordinator(self.store).recover(
                owner_id="owner-a", conversation_id=conversation_id,
                lease_token=token,
            )
        )
        self.assertEqual(summary.interrupted_generations, 1)
        self.assertEqual(self.store.list_recovery_events(
            owner_id="owner-a", conversation_id=conversation_id,
        )[0]["request_id"], "old-recovery-turn")
        self.assertTrue(self.store.has_message(
            owner_id="owner-a", conversation_id=conversation_id,
            idempotency_key="assistant:old-recovery-turn",
        ))

    def test_recovery_saturated_batch_is_reported_without_processing_101st(self):
        conversation_id = self.conversation["conversation_id"]
        version = 0
        for index in range(ConversationRecoveryCoordinator.BATCH_LIMIT + 1):
            result = self._seed_interrupted_generation(f"backlog-{index}", version)
            version = result["version"]

        summary = self._with_recovery_lease(lambda token:
            ConversationRecoveryCoordinator(self.store).recover(
                owner_id="owner-a", conversation_id=conversation_id,
                lease_token=token,
            )
        )
        self.assertEqual(len(summary.recovered_request_ids), 100)
        self.assertTrue(summary.batch_limit_reached)
        remaining = self.store.list_interrupted_generations(
            owner_id="owner-a", conversation_id=conversation_id, limit=100,
        )
        self.assertEqual(len(remaining), 1)
        self.assertEqual(remaining[0]["request_id"], "backlog-100")

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

    def test_long_generation_renews_lease_while_model_is_running(self):
        started = threading.Event()
        renewals = []
        original_renew = self.store.renew_generation_lease

        def recording_renew(**kwargs):
            result = original_renew(**kwargs)
            renewals.append(result)
            return result

        def slow_model(**kwargs):
            started.set()
            threading.Event().wait(0.08)
            return {"text": "renewed response", "tool_calls": {"tool_calls": []}}

        protocol = StructuredToolProtocol(GuardedToolRuntime({}, AllowNothing()))
        service = ConversationService(self.store, AgentExecutionLoop(slow_model, protocol))
        service.LEASE_RENEW_INTERVAL_SECONDS = 0.01
        with patch.object(self.store, "renew_generation_lease", side_effect=recording_renew):
            result = service.reply(
                owner_id="owner-a", conversation_id=self.conversation["conversation_id"],
                user_text="long request", request_id="heartbeat-success",
                expected_version=0, tools=[],
            )
        self.assertTrue(started.is_set())
        self.assertGreaterEqual(len(renewals), 1)
        self.assertEqual(result["answer"], "renewed response")

    def test_lease_cleanup_failure_does_not_mask_execution_error(self):
        service = ConversationService(self.store, self.loop)
        with patch.object(service, "_reply_unleased",
                          side_effect=RuntimeError("original execution failure")):
            with patch.object(self.store, "release_generation_lease",
                              side_effect=OSError("simulated cleanup failure")):
                with self.assertRaisesRegex(RuntimeError, "original execution failure") as caught:
                    service.reply(
                        owner_id="owner-a", conversation_id=self.conversation["conversation_id"],
                        user_text="request", request_id="cleanup-failure",
                        expected_version=0, tools=[],
                    )
        self.assertTrue(any("cleanup also failed" in note for note in getattr(caught.exception, "__notes__", [])))

    def test_lease_cleanup_failure_preserves_errors_without_add_note_support(self):
        service = ConversationService(self.store, self.loop)
        original = RuntimeError("original execution failure")
        original.add_note = None
        with patch.object(service, "_reply_unleased", side_effect=original):
            with patch.object(self.store, "release_generation_lease", side_effect=OSError("simulated cleanup failure")):
                with self.assertRaisesRegex(RuntimeError, "original execution failure") as caught:
                    service.reply(owner_id="owner-a", conversation_id=self.conversation["conversation_id"], user_text="request", request_id="cleanup-no-add-note", expected_version=0, tools=[])
        self.assertIs(caught.exception, original)


    def test_lease_is_released_if_heartbeat_thread_cannot_start(self):
        service = ConversationService(self.store, self.loop)
        with patch("core.conversation_service.threading.Thread.start",
                   side_effect=RuntimeError("simulated thread startup failure")):
            with self.assertRaisesRegex(RuntimeError, "thread startup failure"):
                service.reply(
                    owner_id="owner-a", conversation_id=self.conversation["conversation_id"],
                    user_text="request", request_id="heartbeat-start-failure",
                    expected_version=0, tools=[],
                )
        # The failed startup must not strand the durable lease until TTL expiry.
        self.store.acquire_generation_lease(
            owner_id="owner-a", conversation_id=self.conversation["conversation_id"],
            lease_token="replacement-worker", ttl_seconds=30,
        )
        self.store.release_generation_lease(
            owner_id="owner-a", conversation_id=self.conversation["conversation_id"],
            lease_token="replacement-worker",
        )

    def test_lease_loss_during_tool_batch_blocks_subsequent_side_effects(self):
        first_started = threading.Event()
        first_release = threading.Event()
        invoked = []

        class AllowEcho:
            def authorize_tool(self, name):
                return name == "echo"

        def first_tool(raw):
            invoked.append("first")
            first_started.set()
            if not first_release.wait(timeout=5):
                raise TimeoutError("first tool release timed out")
            return "first completed"

        def second_tool(raw):
            invoked.append("second")
            return "second completed"

        runtime = GuardedToolRuntime(
            {"echo": first_tool, "second": second_tool}, AllowEcho()
        )
        protocol = StructuredToolProtocol(runtime)

        def model(**kwargs):
            return {"text": "", "tool_calls": {"tool_calls": [
                {"id": "call_1", "name": "echo", "arguments": {"value": "one"}},
                {"id": "call_2", "name": "second", "arguments": {"value": "two"}},
            ]}}

        service = ConversationService(
            self.store, AgentExecutionLoop(model, protocol)
        )
        service.LEASE_RENEW_INTERVAL_SECONDS = 0.01

        def failed_renewal(**kwargs):
            raise OSError("simulated durable lease store outage")

        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                with patch.object(self.store, "renew_generation_lease", side_effect=failed_renewal):
                    future = pool.submit(
                        service.reply,
                        owner_id="owner-a",
                        conversation_id=self.conversation["conversation_id"],
                        user_text="Run the two tools",
                        request_id="lease-loss-tool-batch",
                        expected_version=0,
                        tools=[],
                    )
                    self.assertTrue(first_started.wait(timeout=5), "first tool did not start")
                    # Give the heartbeat a deterministic opportunity to observe the
                    # simulated storage outage while the first side effect is blocked.
                    deadline = threading.Event()
                    deadline.wait(0.08)
                    first_release.set()
                    with self.assertRaisesRegex(ConversationConflict, "lease renewal failed"):
                        future.result(timeout=5)
            self.assertEqual(invoked, ["first"])
            history = self.service.history(
                owner_id="owner-a",
                conversation_id=self.conversation["conversation_id"],
            )
            self.assertEqual([item["role"] for item in history], ["user"])
        finally:
            first_release.set()

    def test_lease_renewal_failure_prevents_assistant_persistence(self):
        started = threading.Event()

        def failing_renew(**kwargs):
            raise OSError("simulated lease store outage")

        def slow_model(**kwargs):
            started.set()
            threading.Event().wait(0.08)
            return {"text": "must not be committed", "tool_calls": {"tool_calls": []}}

        protocol = StructuredToolProtocol(GuardedToolRuntime({}, AllowNothing()))
        service = ConversationService(self.store, AgentExecutionLoop(slow_model, protocol))
        service.LEASE_RENEW_INTERVAL_SECONDS = 0.01
        with patch.object(self.store, "renew_generation_lease", side_effect=failing_renew):
            with self.assertRaisesRegex(ConversationConflict, "lease renewal failed"):
                service.reply(
                    owner_id="owner-a", conversation_id=self.conversation["conversation_id"],
                    user_text="request during outage", request_id="heartbeat-failure",
                    expected_version=0, tools=[],
                )
        self.assertTrue(started.is_set())
        history = self.service.history(
            owner_id="owner-a", conversation_id=self.conversation["conversation_id"]
        )
        self.assertEqual([item["role"] for item in history], ["user"])

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


    def test_two_services_racing_to_recover_one_crash_run_model_once(self):
        conversation_id = self.conversation["conversation_id"]
        self.store.append_message(
            owner_id="owner-a", conversation_id=conversation_id,
            role="user", content="Potentially side-effecting task",
            idempotency_key="racing-crashed-request", expected_version=0,
            generation_request_id="racing-crashed-request",
        )
        model_started = threading.Event()
        release_model = threading.Event()
        model_calls = []

        def blocking_model(**kwargs):
            model_calls.append(kwargs)
            model_started.set()
            if not release_model.wait(timeout=5):
                raise TimeoutError("recovery test model release timed out")
            return {"text": "New task completed", "tool_calls": {"tool_calls": []}}

        protocol = StructuredToolProtocol(GuardedToolRuntime({}, AllowNothing()))
        first_service = ConversationService(
            self.store, AgentExecutionLoop(blocking_model, protocol)
        )
        second_service = ConversationService(
            ConversationStore(Path(self.tempdir.name) / "chat.sqlite3"),
            AgentExecutionLoop(blocking_model, protocol),
        )
        start = threading.Barrier(2)

        def submit(service, request_id):
            start.wait(timeout=5)
            return service.reply(
                owner_id="owner-a", conversation_id=conversation_id,
                user_text="Continue safely", request_id=request_id,
                expected_version=1, tools=[],
            )

        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                first = pool.submit(submit, first_service, "recovery-race-a")
                second = pool.submit(submit, second_service, "recovery-race-b")
                self.assertTrue(model_started.wait(timeout=5))
                release_model.set()
                outcomes = []
                for future in (first, second):
                    try:
                        outcomes.append(future.result(timeout=5))
                    except ConversationConflict:
                        outcomes.append("conflict")
        finally:
            release_model.set()

        self.assertEqual(sum(isinstance(value, dict) for value in outcomes), 1)
        self.assertEqual(outcomes.count("conflict"), 1)
        self.assertEqual(len(model_calls), 1)
        history = self.store.list_messages(
            owner_id="owner-a", conversation_id=conversation_id, limit=20,
        )
        self.assertEqual(
            [message["role"] for message in history],
            ["user", "assistant", "user", "assistant"],
        )
        self.assertIn("interrupted", history[1]["content"])
        self.assertEqual(
            self.store.list_interrupted_generations(
                owner_id="owner-a", conversation_id=conversation_id,
            ),
            [],
        )

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

    def test_recovery_closes_generation_when_user_message_row_is_missing(self):
        self.store.append_message(
            owner_id="owner-a", conversation_id=self.conversation["conversation_id"],
            role="user", content="Orphaned generation",
            idempotency_key="orphaned-request", expected_version=0,
            generation_request_id="orphaned-request",
        )
        with patch.object(self.store, "list_messages", return_value=[]):
            result = self.service.reply(
                owner_id="owner-a",
                conversation_id=self.conversation["conversation_id"],
                user_text="A new request",
                request_id="after-orphan",
                expected_version=1,
                tools=[],
            )
        self.assertEqual(result["status"], "completed")
        self.assertEqual(
            self.store.list_interrupted_generations(
                owner_id="owner-a",
                conversation_id=self.conversation["conversation_id"],
            ),
            [],
        )
        self.assertEqual(len(self.observed), 1)

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


    def test_recovery_matches_assistant_to_its_own_generation_not_next_turn(self):
        first = self.store.append_message(
            owner_id="owner-a", conversation_id=self.conversation["conversation_id"],
            role="user", content="First interrupted turn", idempotency_key="first-run",
            expected_version=0, generation_request_id="first-run",
        )
        second = self.store.append_message(
            owner_id="owner-a", conversation_id=self.conversation["conversation_id"],
            role="user", content="Second interrupted turn", idempotency_key="second-run",
            expected_version=first["version"], generation_request_id="second-run",
        )
        self.store.append_message(
            owner_id="owner-a", conversation_id=self.conversation["conversation_id"],
            role="assistant", content="First answer persisted before crash",
            idempotency_key="assistant:first-run", expected_version=second["version"],
        )

        result = self.service.reply(
            owner_id="owner-a", conversation_id=self.conversation["conversation_id"],
            user_text="A fresh request", request_id="fresh-after-two-crashes",
            expected_version=3, tools=[],
        )

        self.assertEqual(result["status"], "completed")
        generations = self.store.list_interrupted_generations(
            owner_id="owner-a", conversation_id=self.conversation["conversation_id"],
        )
        self.assertEqual(generations, [])
        history = self.service.history(
            owner_id="owner-a", conversation_id=self.conversation["conversation_id"],
        )
        self.assertEqual(history[2]["content"], "First answer persisted before crash")
        self.assertIn("interrupted", history[3]["content"])
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


    def test_reply_journals_tool_call_before_and_after_guarded_side_effect(self):
        invoked = []

        class AllowEcho:
            def authorize_tool(self, name):
                return name == "echo"

        model_calls = []
        def model(**kwargs):
            model_calls.append(True)
            if len(model_calls) > 1:
                return {"text": "Echo completed", "tool_calls": {"tool_calls": []}}
            return {"text": "", "tool_calls": {"tool_calls": [
                {"id": "echo-1", "name": "echo", "arguments": {"value": "hello"}},
            ]}}

        runtime = GuardedToolRuntime(
            {"echo": lambda raw: invoked.append(raw) or "ok"}, AllowEcho()
        )
        service = ConversationService(
            self.store, AgentExecutionLoop(model, StructuredToolProtocol(runtime))
        )
        result = service.reply(
            owner_id="owner-a",
            conversation_id=self.conversation["conversation_id"],
            user_text="Run echo",
            request_id="journalled-tool-request",
            expected_version=0,
            tools=[],
        )
        self.assertEqual(result["status"], "completed")
        self.assertEqual(len(invoked), 1)
        records = self.store.list_tool_executions(
            owner_id="owner-a",
            conversation_id=self.conversation["conversation_id"],
            request_id="journalled-tool-request",
        )
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["call_id"], "echo-1")
        self.assertEqual(records[0]["tool_name"], "echo")
        self.assertEqual(records[0]["status"], "completed")


    def test_handler_exception_is_journaled_uncertain_and_stops_batch(self):
        executed = []

        class AllowEcho:
            def authorize_tool(self, name):
                return name == "echo"

        def model(**kwargs):
            return {"text": "", "tool_calls": {"tool_calls": [
                {"id": "uncertain-echo", "name": "echo", "arguments": {"value": "x"}},
                {"id": "must-not-run", "name": "echo", "arguments": {"value": "y"}},
            ]}}

        def handler(raw):
            executed.append(raw)
            raise RuntimeError("remote acknowledgement lost")

        runtime = GuardedToolRuntime({"echo": handler}, AllowEcho())
        service = ConversationService(
            self.store, AgentExecutionLoop(model, StructuredToolProtocol(runtime))
        )
        result = service.reply(
            owner_id="owner-a",
            conversation_id=self.conversation["conversation_id"],
            user_text="Run uncertain echo",
            request_id="uncertain-handler-request",
            expected_version=0,
            tools=[],
        )
        self.assertEqual(result["status"], "interrupted")
        self.assertEqual(len(executed), 1)
        records = self.store.list_tool_executions(
            owner_id="owner-a",
            conversation_id=self.conversation["conversation_id"],
            request_id="uncertain-handler-request",
        )
        self.assertEqual([item["status"] for item in records], ["uncertain"])
        self.assertEqual([item["call_id"] for item in records], ["uncertain-echo"])


    def test_provider_idempotency_key_is_forwarded_and_durably_recorded(self):
        received_keys = []

        class AllowSend:
            def authorize_tool(self, name):
                return name == "send"

        def handler(raw, *, idempotency_key):
            received_keys.append(idempotency_key)
            return "accepted"

        handler.supports_idempotency_key = True

        model_calls = []
        def model(**kwargs):
            model_calls.append(True)
            if len(model_calls) > 1:
                return {"text": "Message sent", "tool_calls": {"tool_calls": []}}
            return {"text": "", "tool_calls": {"tool_calls": [
                {"id": "provider-send-1", "name": "send", "arguments": {"to": "user"}},
            ]}}

        runtime = GuardedToolRuntime({"send": handler}, AllowSend())
        service = ConversationService(
            self.store, AgentExecutionLoop(model, StructuredToolProtocol(runtime))
        )
        result = service.reply(
            owner_id="owner-a",
            conversation_id=self.conversation["conversation_id"],
            user_text="Send safely",
            request_id="provider-idempotency-request",
            expected_version=0,
            tools=[],
        )
        self.assertEqual(result["status"], "completed")
        self.assertEqual(len(received_keys), 1)
        self.assertEqual(len(received_keys[0]), 64)
        record = self.store.list_tool_executions(
            owner_id="owner-a",
            conversation_id=self.conversation["conversation_id"],
            request_id="provider-idempotency-request",
        )[0]
        self.assertTrue(record["provider_idempotency"])
        self.assertEqual(record["idempotency_key"], received_keys[0])
        self.assertEqual(record["status"], "completed")

    def test_crash_after_external_side_effect_before_journal_commit_never_replays(self):
        executed = []

        class AllowSend:
            def authorize_tool(self, name):
                return name == "send"

        model_calls = []

        def model(**kwargs):
            model_calls.append(True)
            if len(model_calls) > 1:
                return {"text": "New request completed", "tool_calls": {"tool_calls": []}}
            return {"text": "", "tool_calls": {"tool_calls": [
                {"id": "send-once", "name": "send", "arguments": {"message": "hello"}},
            ]}}

        runtime = GuardedToolRuntime(
            {"send": lambda raw: executed.append(raw) or "accepted"}, AllowSend()
        )
        service = ConversationService(
            self.store, AgentExecutionLoop(model, StructuredToolProtocol(runtime))
        )
        original_finish = self.store.finish_tool_execution
        crash_once = {"pending": True}

        def crash_after_side_effect(**kwargs):
            if crash_once["pending"]:
                crash_once["pending"] = False
                raise SystemExit("simulated process termination after provider success")
            return original_finish(**kwargs)

        with patch.object(self.store, "finish_tool_execution", side_effect=crash_after_side_effect):
            with self.assertRaisesRegex(SystemExit, "simulated process termination"):
                service.reply(
                    owner_id="owner-a",
                    conversation_id=self.conversation["conversation_id"],
                    user_text="Send once",
                    request_id="crash-window-request",
                    expected_version=0,
                    tools=[],
                )

        self.assertEqual(len(executed), 1)
        unresolved = self.store.list_tool_executions(
            owner_id="owner-a",
            conversation_id=self.conversation["conversation_id"],
            request_id="crash-window-request",
        )
        self.assertEqual([item["status"] for item in unresolved], ["started"])

        recovered = service.reply(
            owner_id="owner-a",
            conversation_id=self.conversation["conversation_id"],
            user_text="Continue safely",
            request_id="after-crash-window",
            expected_version=1,
            tools=[],
        )
        self.assertEqual(recovered["status"], "completed")
        self.assertEqual(len(executed), 1)
        resolved = self.store.list_tool_executions(
            owner_id="owner-a",
            conversation_id=self.conversation["conversation_id"],
            request_id="crash-window-request",
        )
        self.assertEqual([item["status"] for item in resolved], ["uncertain"])
        self.assertEqual(len(model_calls), 2)


    def test_only_one_concurrent_worker_can_claim_and_recover_generation(self):
        conversation_id = self.conversation["conversation_id"]
        self._seed_interrupted_generation("concurrent-recovery-claim", 0)
        barrier = threading.Barrier(2)

        def claim(token):
            barrier.wait(timeout=5)
            try:
                self.store.acquire_generation_lease(
                    owner_id="owner-a", conversation_id=conversation_id,
                    lease_token=token, ttl_seconds=60,
                )
                return token
            except ConversationConflict:
                return None

        tokens = ("recovery-worker-a", "recovery-worker-b")
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(claim, tokens))

        winners = [token for token in outcomes if token is not None]
        self.assertEqual(len(winners), 1)
        winner = winners[0]
        try:
            summary = ConversationRecoveryCoordinator(self.store).recover(
                owner_id="owner-a", conversation_id=conversation_id,
                lease_token=winner,
            )
        finally:
            self.store.release_generation_lease(
                owner_id="owner-a", conversation_id=conversation_id,
                lease_token=winner,
            )

        self.assertEqual(summary.interrupted_generations, 1)
        events = self.store.list_recovery_events(
            owner_id="owner-a", conversation_id=conversation_id,
        )
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["request_id"], "concurrent-recovery-claim")
        self.assertEqual(events[0]["outcome"], "interrupted")



    def test_live_generation_lease_renewal_preserves_fencing_and_exclusivity(self):
        conversation_id = self.conversation["conversation_id"]
        token = "renewable-recovery-worker"
        self.store.acquire_generation_lease(
            owner_id="owner-a", conversation_id=conversation_id,
            lease_token=token, ttl_seconds=5,
        )
        try:
            renewed = self.store.renew_generation_lease(
                owner_id="owner-a", conversation_id=conversation_id,
                lease_token=token, ttl_seconds=60,
            )
            self.assertGreater(renewed["expires_at"], 0)
            with self.assertRaises(ConversationConflict):
                self.store.renew_generation_lease(
                    owner_id="owner-a", conversation_id=conversation_id,
                    lease_token="stale-worker-token", ttl_seconds=60,
                )
            with self.assertRaises(ConversationConflict):
                self.store.acquire_generation_lease(
                    owner_id="owner-a", conversation_id=conversation_id,
                    lease_token="competing-worker-token", ttl_seconds=60,
                )
        finally:
            self.store.release_generation_lease(
                owner_id="owner-a", conversation_id=conversation_id,
                lease_token=token,
            )



    def test_expired_worker_cannot_renew_or_release_replacement_lease(self):
        conversation_id = self.conversation["conversation_id"]
        with patch("core.conversation_store.time.time", side_effect=[100.0, 106.0, 107.0, 108.0]):
            self.store.acquire_generation_lease(
                owner_id="owner-a", conversation_id=conversation_id,
                lease_token="expired-worker", ttl_seconds=5,
            )
            self.store.acquire_generation_lease(
                owner_id="owner-a", conversation_id=conversation_id,
                lease_token="replacement-worker", ttl_seconds=60,
            )
            with self.assertRaises(ConversationConflict):
                self.store.renew_generation_lease(
                    owner_id="owner-a", conversation_id=conversation_id,
                    lease_token="expired-worker", ttl_seconds=60,
                )
            self.assertFalse(self.store.release_generation_lease(
                owner_id="owner-a", conversation_id=conversation_id,
                lease_token="expired-worker",
            ))
            with self.assertRaises(ConversationConflict):
                self.store.acquire_generation_lease(
                    owner_id="owner-a", conversation_id=conversation_id,
                    lease_token="third-worker", ttl_seconds=60,
                )
            self.assertTrue(self.store.release_generation_lease(
                owner_id="owner-a", conversation_id=conversation_id,
                lease_token="replacement-worker",
            ))



    def test_service_heartbeat_renews_lease_during_slow_recovery(self):
        entered_recovery = threading.Event()
        allow_recovery_to_finish = threading.Event()
        lease_renewed = threading.Event()
        original_recover = self.service.recovery_coordinator.recover
        original_renew = self.store.renew_generation_lease

        def slow_recovery(**kwargs):
            entered_recovery.set()
            if not allow_recovery_to_finish.wait(timeout=5):
                raise TimeoutError("test did not release recovery")
            return original_recover(**kwargs)

        def observe_renewal(**kwargs):
            result = original_renew(**kwargs)
            lease_renewed.set()
            return result

        self.service.LEASE_TTL_SECONDS = 5
        self.service.LEASE_RENEW_INTERVAL_SECONDS = 0.05
        with patch.object(self.service.recovery_coordinator, "recover", side_effect=slow_recovery), \
             patch.object(self.store, "renew_generation_lease", side_effect=observe_renewal):
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(
                    self.service.reply,
                    owner_id="owner-a",
                    conversation_id=self.conversation["conversation_id"],
                    user_text="Continue after recovery",
                    request_id="heartbeat-during-recovery",
                    expected_version=0,
                    tools=[],
                )
                self.assertTrue(entered_recovery.wait(timeout=2))
                try:
                    self.assertTrue(lease_renewed.wait(timeout=2))
                finally:
                    allow_recovery_to_finish.set()
                result = future.result(timeout=5)

        self.assertEqual(result["status"], "completed")
        self.assertTrue(lease_renewed.is_set())



    def test_heartbeat_renewal_failure_during_recovery_prevents_response_commit(self):
        entered_recovery = threading.Event()
        allow_recovery_to_finish = threading.Event()
        renewal_attempted = threading.Event()
        original_recover = self.service.recovery_coordinator.recover

        def slow_recovery(**kwargs):
            entered_recovery.set()
            if not allow_recovery_to_finish.wait(timeout=5):
                raise TimeoutError("test did not release recovery")
            return original_recover(**kwargs)

        def fail_renewal(**kwargs):
            renewal_attempted.set()
            raise OSError("simulated lease store outage")

        self.service.LEASE_TTL_SECONDS = 5
        self.service.LEASE_RENEW_INTERVAL_SECONDS = 0.01
        with patch.object(self.service.recovery_coordinator, "recover", side_effect=slow_recovery), \
             patch.object(self.store, "renew_generation_lease", side_effect=fail_renewal):
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(
                    self.service.reply,
                    owner_id="owner-a",
                    conversation_id=self.conversation["conversation_id"],
                    user_text="Do not commit without lease proof",
                    request_id="heartbeat-failure-during-recovery",
                    expected_version=0,
                    tools=[],
                )
                self.assertTrue(entered_recovery.wait(timeout=2))
                try:
                    self.assertTrue(renewal_attempted.wait(timeout=2))
                finally:
                    allow_recovery_to_finish.set()
                with self.assertRaisesRegex(ConversationConflict, "renewal failed"):
                    future.result(timeout=5)

        self.assertEqual(self.observed, [])
        self.assertFalse(self.store.has_message(
            owner_id="owner-a",
            conversation_id=self.conversation["conversation_id"],
            idempotency_key="assistant:heartbeat-failure-during-recovery",
        ))



    def test_replaced_lease_during_recovery_blocks_stale_worker_commit(self):
        entered_recovery = threading.Event()
        allow_recovery_to_finish = threading.Event()
        original_recover = self.service.recovery_coordinator.recover
        renewal_rejected = threading.Event()
        original_renew = self.store.renew_generation_lease

        def blocked_recovery(**kwargs):
            entered_recovery.set()
            if not allow_recovery_to_finish.wait(timeout=5):
                raise TimeoutError("test did not release recovery")
            return original_recover(**kwargs)

        def observe_rejected_renewal(**kwargs):
            try:
                return original_renew(**kwargs)
            finally:
                renewal_rejected.set()

        self.service.LEASE_TTL_SECONDS = 60
        self.service.LEASE_RENEW_INTERVAL_SECONDS = 0.01
        with patch.object(
            self.service.recovery_coordinator, "recover",
            side_effect=blocked_recovery,
        ), patch.object(self.store, "renew_generation_lease", side_effect=observe_rejected_renewal):
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(
                    self.service.reply,
                    owner_id="owner-a",
                    conversation_id=self.conversation["conversation_id"],
                    user_text="Must not commit after losing the lease",
                    request_id="stale-worker-recovery",
                    expected_version=0,
                    tools=[],
                )
                self.assertTrue(entered_recovery.wait(timeout=2))
                with self.store._connect() as db:
                    db.execute(
                        """UPDATE conversation_generation_leases
                           SET lease_token=?, expires_at=?
                           WHERE owner_id=? AND conversation_id=?""",
                        ("replacement-during-recovery", 9999999999,
                         "owner-a", self.conversation["conversation_id"]),
                    )
                try:
                    self.assertTrue(renewal_rejected.wait(timeout=2))
                finally:
                    allow_recovery_to_finish.set()
                with self.assertRaises(ConversationConflict):
                    future.result(timeout=5)

        self.assertEqual(self.observed, [])
        self.assertFalse(self.store.has_message(
            owner_id="owner-a",
            conversation_id=self.conversation["conversation_id"],
            idempotency_key="assistant:stale-worker-recovery",
        ))


    def test_recovery_process_crash_preserves_uncertain_tool_outcome_without_replay(self):
        database_path = Path(self.tempdir.name) / "chat.sqlite3"
        conversation_id = self.conversation["conversation_id"]
        request_id = "recovery-crash-after-uncertain"
        self._seed_interrupted_generation(request_id, 0)

        initial_token = "seed-tool-execution-lease"
        self.store.acquire_generation_lease(
            owner_id="owner-a", conversation_id=conversation_id,
            lease_token=initial_token, ttl_seconds=60,
        )
        try:
            self.store.begin_tool_execution(
                owner_id="owner-a", conversation_id=conversation_id,
                request_id=request_id, call_id="send-once", tool_name="send",
                arguments_sha256="a" * 64, lease_token=initial_token,
            )
        finally:
            self.store.release_generation_lease(
                owner_id="owner-a", conversation_id=conversation_id,
                lease_token=initial_token,
            )

        context = multiprocessing.get_context("spawn")
        parent_connection, child_connection = context.Pipe(duplex=False)
        worker = context.Process(
            target=_multiprocess_mark_uncertain_then_crash,
            args=(str(database_path), conversation_id, request_id, child_connection),
        )
        worker.start()
        child_connection.close()
        try:
            self.assertEqual(parent_connection.recv(), ("marked-uncertain", 1))
            worker.join(timeout=10)
            self.assertFalse(worker.is_alive(), "recovery worker did not exit")
            self.assertEqual(worker.exitcode, 0)
        finally:
            parent_connection.close()
            if worker.is_alive():
                worker.terminate()
                worker.join(timeout=5)

        records_before_retry = self.store.list_tool_executions(
            owner_id="owner-a", conversation_id=conversation_id,
            request_id=request_id,
        )
        self.assertEqual([record["status"] for record in records_before_retry], ["uncertain"])

        replacement_token = "replacement-recovery-worker"
        self.store.acquire_generation_lease(
            owner_id="owner-a", conversation_id=conversation_id,
            lease_token=replacement_token, ttl_seconds=60,
        )
        try:
            summary = ConversationRecoveryCoordinator(self.store).recover(
                owner_id="owner-a", conversation_id=conversation_id,
                lease_token=replacement_token,
            )
        finally:
            self.store.release_generation_lease(
                owner_id="owner-a", conversation_id=conversation_id,
                lease_token=replacement_token,
            )

        self.assertEqual(summary.interrupted_generations, 1)
        records_after_retry = self.store.list_tool_executions(
            owner_id="owner-a", conversation_id=conversation_id,
            request_id=request_id,
        )
        self.assertEqual([record["status"] for record in records_after_retry], ["uncertain"])
        events = self.store.list_recovery_events(
            owner_id="owner-a", conversation_id=conversation_id,
        )
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["request_id"], request_id)
        self.assertEqual(events[0]["uncertain_tool_calls"], 1)

    def test_expired_lease_can_be_reclaimed_after_worker_process_crashes(self):
        database_path = Path(self.tempdir.name) / "chat.sqlite3"
        conversation_id = self.conversation["conversation_id"]
        context = multiprocessing.get_context("spawn")
        parent_connection, child_connection = context.Pipe(duplex=False)
        worker = context.Process(
            target=_multiprocess_crash_after_generation_lease,
            args=(
                str(database_path), conversation_id,
                "crashed-process-token", child_connection,
            ),
        )
        worker.start()
        child_connection.close()
        try:
            self.assertEqual(parent_connection.recv(), "lease-acquired")
            worker.join(timeout=10)
            self.assertFalse(worker.is_alive(), "crashed lease worker did not exit")
            self.assertEqual(worker.exitcode, 0)
        finally:
            parent_connection.close()
            if worker.is_alive():
                worker.terminate()
                worker.join(timeout=5)

        replacement = self.store.acquire_generation_lease(
            owner_id="owner-a", conversation_id=conversation_id,
            lease_token="replacement-after-process-crash", ttl_seconds=60,
        )
        self.assertEqual(
            replacement["lease_token"], "replacement-after-process-crash",
        )
        self.store.release_generation_lease(
            owner_id="owner-a", conversation_id=conversation_id,
            lease_token="replacement-after-process-crash",
        )

    def test_separate_processes_cannot_both_acquire_generation_lease(self):
        database_path = Path(self.tempdir.name) / "chat.sqlite3"
        conversation_id = self.conversation["conversation_id"]
        context = multiprocessing.get_context("spawn")
        barrier = context.Barrier(2)
        results = context.Queue()
        workers = [
            context.Process(
                target=_multiprocess_claim_generation_lease,
                args=(str(database_path), conversation_id, token, barrier, results),
            )
            for token in ("process-worker-a", "process-worker-b")
        ]

        for worker in workers:
            worker.start()
        try:
            outcomes = [results.get(timeout=20) for _ in workers]
            for worker in workers:
                worker.join(timeout=20)
                self.assertFalse(worker.is_alive(), "lease worker process did not exit")
                self.assertEqual(worker.exitcode, 0)
        finally:
            for worker in workers:
                if worker.is_alive():
                    worker.terminate()
                    worker.join(timeout=5)
            results.close()
            results.join_thread()

        self.assertEqual(
            sorted(outcome for outcome, _token in outcomes),
            ["acquired", "conflict"],
        )
        self.assertEqual(
            {token for _outcome, token in outcomes},
            {"process-worker-a", "process-worker-b"},
        )

    def test_independent_store_instances_share_lease_and_recovery_state(self):
        database_path = Path(self.tempdir.name) / "chat.sqlite3"
        second_store = ConversationStore(database_path)
        conversation_id = self.conversation["conversation_id"]
        barrier = threading.Barrier(2)

        def claim(store, token):
            barrier.wait(timeout=5)
            try:
                store.acquire_generation_lease(
                    owner_id="owner-a", conversation_id=conversation_id,
                    lease_token=token, ttl_seconds=60,
                )
                return token
            except ConversationConflict:
                return None

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            futures = (
                pool.submit(claim, self.store, "independent-store-a"),
                pool.submit(claim, second_store, "independent-store-b"),
            )
            winners = [future.result(timeout=10) for future in futures]

        winning_tokens = [token for token in winners if token is not None]
        self.assertEqual(len(winning_tokens), 1)
        winning_token = winning_tokens[0]
        winning_store = (
            self.store if winning_token == "independent-store-a" else second_store
        )
        winning_store.release_generation_lease(
            owner_id="owner-a", conversation_id=conversation_id,
            lease_token=winning_token,
        )

        self._seed_interrupted_generation("cross-instance-recovery", 0)
        recovery_token = "cross-instance-recovery-worker"
        second_store.acquire_generation_lease(
            owner_id="owner-a", conversation_id=conversation_id,
            lease_token=recovery_token, ttl_seconds=60,
        )
        try:
            summary = ConversationRecoveryCoordinator(second_store).recover(
                owner_id="owner-a", conversation_id=conversation_id,
                lease_token=recovery_token,
            )
        finally:
            second_store.release_generation_lease(
                owner_id="owner-a", conversation_id=conversation_id,
                lease_token=recovery_token,
            )

        self.assertEqual(summary.interrupted_generations, 1)
        events_seen_by_first_store = self.store.list_recovery_events(
            owner_id="owner-a", conversation_id=conversation_id,
        )
        self.assertEqual(len(events_seen_by_first_store), 1)
        self.assertEqual(
            events_seen_by_first_store[0]["request_id"],
            "cross-instance-recovery",
        )


if __name__ == "__main__":
    unittest.main()
