import unittest
from datetime import datetime, timezone

from core.provider_outcome import (
    ProviderOutcome, ProviderOutcomeEvidence, verify_provider_outcome,
)


class ProviderOutcomeTests(unittest.TestCase):
    def setUp(self):
        self.execution = {
            "execution_id": "execution-1",
            "idempotency_key": "a" * 64,
            "arguments_sha256": "b" * 64,
            "status": "uncertain",
        }

    def evidence(self, **overrides):
        values = {
            "provider": "mail.example",
            "outcome": ProviderOutcome.UNKNOWN,
            "idempotency_key": "a" * 64,
            "arguments_sha256": "b" * 64,
            "evidence_sha256": "c" * 64,
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "provider_reference": "message-123",
        }
        values.update(overrides)
        return ProviderOutcomeEvidence(**values)

    def test_explicit_adapter_returns_typed_unknown_without_retry(self):
        calls = []
        evidence = self.evidence()
        class Adapter:
            provider_id = "mail.example"

            def lookup_tool_outcome(self, execution):
                calls.append(execution["execution_id"])
                return evidence
        result = verify_provider_outcome(Adapter(), self.execution)
        self.assertEqual(result.outcome, ProviderOutcome.UNKNOWN)
        self.assertEqual(calls, ["execution-1"])

    def test_adapter_receives_only_minimum_execution_identity(self):
        received = []
        class Adapter:
            provider_id = "mail.example"

            def lookup_tool_outcome(self, execution):
                received.append(execution)
                return ProviderOutcomeTests().evidence()
        sensitive_execution = {
            **self.execution,
            "owner_id": "private-owner",
            "conversation_id": "private-conversation",
            "arguments": {"to": "private@example.test", "body": "sensitive"},
            "provider_credentials": "must-not-cross-boundary",
        }
        verify_provider_outcome(Adapter(), sensitive_execution)
        self.assertEqual(received, [{
            "execution_id": "execution-1",
            "idempotency_key": "a" * 64,
            "arguments_sha256": "b" * 64,
            "status": "uncertain",
        }])

    def test_expected_provider_identity_is_enforced(self):
        class Adapter:
            def lookup_tool_outcome(self, execution):
                return ProviderOutcomeTests().evidence(provider="other.mail")

        with self.assertRaisesRegex(ValueError, "registered provider identity"):
            verify_provider_outcome(
                Adapter(), self.execution, expected_provider="mail.example",
            )

    def test_mismatched_idempotency_key_is_rejected(self):
        class Adapter:
            provider_id = "mail.example"

            def lookup_tool_outcome(self, execution):
                return ProviderOutcomeTests().evidence(idempotency_key="d" * 64)
        with self.assertRaisesRegex(ValueError, "fingerprint"):
            verify_provider_outcome(Adapter(), self.execution)

    def test_malformed_execution_identity_is_rejected_before_adapter_lookup(self):
        calls = []

        class Adapter:
            provider_id = "mail.example"

            def lookup_tool_outcome(self, execution):
                calls.append(execution)
                return ProviderOutcomeTests().evidence()

        malformed = (
            {"execution_id": "", "idempotency_key": "a" * 64,
             "arguments_sha256": "b" * 64, "status": "uncertain"},
            {"execution_id": "x" * 257, "idempotency_key": "a" * 64,
             "arguments_sha256": "b" * 64, "status": "uncertain"},
            {"execution_id": "execution-1", "idempotency_key": ["a" * 64],
             "arguments_sha256": "b" * 64, "status": "uncertain"},
            {"execution_id": "execution-1", "idempotency_key": "a" * 64,
             "arguments_sha256": "not-a-digest", "status": "uncertain"},
            {"execution_id": "execution-1", "idempotency_key": "a" * 64,
             "arguments_sha256": "b" * 64, "status": []},
        )
        for execution in malformed:
            with self.subTest(execution=execution):
                with self.assertRaises(ValueError):
                    verify_provider_outcome(Adapter(), execution)
        self.assertEqual(calls, [])

    def test_mismatched_argument_fingerprint_is_rejected(self):
        class Adapter:
            provider_id = "mail.example"

            def lookup_tool_outcome(self, execution):
                return ProviderOutcomeTests().evidence(arguments_sha256="d" * 64)

        with self.assertRaisesRegex(ValueError, "fingerprint"):
            verify_provider_outcome(Adapter(), self.execution)

    def test_adapter_failure_does_not_trigger_a_second_lookup(self):
        calls = []

        class Adapter:
            provider_id = "mail.example"

            def lookup_tool_outcome(self, execution):
                calls.append(execution["execution_id"])
                raise TimeoutError("provider lookup timed out")

        with self.assertRaisesRegex(TimeoutError, "timed out"):
            verify_provider_outcome(Adapter(), self.execution)
        self.assertEqual(calls, ["execution-1"])

    def test_untyped_adapter_result_is_rejected(self):
        class Adapter:
            provider_id = "mail.example"

            def lookup_tool_outcome(self, execution):
                return {"outcome": "confirmed_completed"}
        with self.assertRaisesRegex(TypeError, "untyped"):
            verify_provider_outcome(Adapter(), self.execution)

    def test_lookup_rejected_for_already_completed_execution(self):
        class Adapter:
            provider_id = "mail.example"

            def lookup_tool_outcome(self, execution):
                raise AssertionError("must not query")
        execution = {**self.execution, "status": "completed"}
        with self.assertRaisesRegex(ValueError, "started or uncertain"):
            verify_provider_outcome(Adapter(), execution)

    def test_outcome_and_evidence_digest_are_validated(self):
        with self.assertRaisesRegex(ValueError, "ProviderOutcome"):
            self.evidence(outcome="completed")
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            self.evidence(evidence_sha256="not-a-digest")

    def test_future_dated_provider_evidence_is_rejected(self):
        from datetime import timedelta
        future = (datetime.now(timezone.utc) + timedelta(minutes=6)).isoformat()
        with self.assertRaisesRegex(ValueError, "five minutes"):
            self.evidence(observed_at=future)

    def test_delayed_provider_evidence_remains_valid(self):
        from datetime import timedelta
        old = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        self.assertEqual(self.evidence(observed_at=old).observed_at, old)



    def test_reconciler_rejects_malformed_durable_record_before_claim(self):
        from core.provider_outcome import ProviderOutcomeReconciler, ProviderOutcomeRegistry

        class Adapter:
            provider_id = "mail.example"

            def lookup_tool_outcome(self, execution):
                raise AssertionError("adapter must not be called")

        class Store:
            claimed = False

            def list_tool_executions(self, **kwargs):
                return [{
                    "execution_id": "execution-1", "call_id": "call-1",
                    "tool_name": "send_email", "idempotency_key": "invalid",
                    "arguments_sha256": "b" * 64, "status": "uncertain",
                }]

            def acquire_provider_reconciliation_lease(self, **kwargs):
                self.claimed = True

            def renew_provider_reconciliation_lease(self, **kwargs):
                raise AssertionError("no heartbeat expected")

            def release_provider_reconciliation_lease(self, **kwargs):
                raise AssertionError("no claim to release")

            def record_provider_outcome_evidence(self, **kwargs):
                raise AssertionError("no evidence to persist")

        store = Store()
        reconciler = ProviderOutcomeReconciler(
            store, ProviderOutcomeRegistry({"send_email": Adapter()}),
        )
        with self.assertRaisesRegex(ValueError, "idempotency_key"):
            reconciler.reconcile(
                owner_id="owner-1", conversation_id="conversation-1",
                request_id="request-1", call_id="call-1",
            )
        self.assertFalse(store.claimed)

    def test_reconciler_rejects_ambiguous_duplicate_call_records_before_claim(self):
        from core.provider_outcome import ProviderOutcomeReconciler, ProviderOutcomeRegistry

        class Adapter:
            provider_id = "mail.example"

            def lookup_tool_outcome(self, execution):
                raise AssertionError("adapter must not be called")

        class Store:
            claimed = False

            def list_tool_executions(self, **kwargs):
                record = {
                    "execution_id": "execution-1", "call_id": "call-1",
                    "tool_name": "send_email", "idempotency_key": "a" * 64,
                    "arguments_sha256": "b" * 64, "status": "uncertain",
                }
                return [record, dict(record)]

            def acquire_provider_reconciliation_lease(self, **kwargs):
                self.claimed = True

            def renew_provider_reconciliation_lease(self, **kwargs):
                raise AssertionError("no heartbeat expected")

            def release_provider_reconciliation_lease(self, **kwargs):
                raise AssertionError("no claim to release")

            def record_provider_outcome_evidence(self, **kwargs):
                raise AssertionError("no evidence to persist")

        store = Store()
        reconciler = ProviderOutcomeReconciler(
            store, ProviderOutcomeRegistry({"send_email": Adapter()}),
        )
        with self.assertRaisesRegex(ValueError, "ambiguous"):
            reconciler.reconcile(
                owner_id="owner-1", conversation_id="conversation-1",
                request_id="request-1", call_id="call-1",
            )
        self.assertFalse(store.claimed)

    def test_registry_rejects_malformed_lookup_tool_names(self):
        from core.provider_outcome import ProviderOutcomeRegistry

        class Adapter:
            provider_id = "mail.example"

            def lookup_tool_outcome(self, execution):
                return ProviderOutcomeTests().evidence()

        registry = ProviderOutcomeRegistry({"send_email": Adapter()})
        for tool_name in ([], {}, "", "x" * 129):
            with self.subTest(tool_name=tool_name):
                with self.assertRaises(ValueError):
                    registry.lookup(tool_name, self.execution)

    def test_registry_freezes_lookup_callable_at_registration(self):
        from core.provider_outcome import ProviderOutcomeRegistry

        calls = []
        class Adapter:
            provider_id = "mail.example"

            def lookup_tool_outcome(self, execution):
                calls.append("registered")
                return ProviderOutcomeTests().evidence()

        adapter = Adapter()
        registry = ProviderOutcomeRegistry({"send_email": adapter})

        def replaced_lookup(execution):
            calls.append("replaced")
            raise AssertionError("mutated adapter method must not be used")

        adapter.lookup_tool_outcome = replaced_lookup
        result = registry.lookup("send_email", self.execution)
        self.assertEqual(result.provider, "mail.example")
        self.assertEqual(calls, ["registered"])

    def test_registry_mapping_cannot_be_mutated_after_registration(self):
        from core.provider_outcome import ProviderOutcomeRegistry

        class Adapter:
            provider_id = "mail.example"

            def lookup_tool_outcome(self, execution):
                return ProviderOutcomeTests().evidence()

        registry = ProviderOutcomeRegistry({"send_email": Adapter()})
        with self.assertRaises(TypeError):
            registry._adapters["send_email"] = (Adapter(), "attacker.example")

    def test_direct_verification_rejects_malformed_expected_provider(self):
        class Adapter:
            def lookup_tool_outcome(self, execution):
                raise AssertionError("must reject configuration before lookup")

        with self.assertRaisesRegex(ValueError, "expected_provider"):
            verify_provider_outcome(Adapter(), self.execution, expected_provider=[])

    def test_registry_freezes_provider_identity_at_registration(self):
        from core.provider_outcome import ProviderOutcomeRegistry

        class Adapter:
            provider_id = "mail.example"

            def lookup_tool_outcome(self, execution):
                return ProviderOutcomeTests().evidence(provider="mail.example")

        adapter = Adapter()
        registry = ProviderOutcomeRegistry({"send_email": adapter})
        adapter.provider_id = "attacker.example"
        result = registry.lookup("send_email", self.execution)
        self.assertEqual(result.provider, "mail.example")

    def test_registry_rejects_adapter_without_explicit_provider_identity(self):
        from core.provider_outcome import ProviderOutcomeRegistry

        class Adapter:
            def lookup_tool_outcome(self, execution):
                return ProviderOutcomeTests().evidence()

        with self.assertRaisesRegex(ValueError, "provider_id"):
            ProviderOutcomeRegistry({"send_email": Adapter()})

    def test_registry_requires_explicit_adapter_and_reconciler_persists_evidence(self):
        from core.provider_outcome import ProviderOutcomeReconciler, ProviderOutcomeRegistry
        calls = []
        evidence = self.evidence()
        class Adapter:
            provider_id = "mail.example"

            def lookup_tool_outcome(self, execution):
                calls.append(dict(execution))
                return evidence
        class Store:
            def list_tool_executions(self, **kwargs):
                return [{
                    "execution_id": "execution-1", "call_id": "call-1",
                    "tool_name": "send_email", "idempotency_key": "a" * 64,
                    "arguments_sha256": "b" * 64, "status": "uncertain",
                }]
            def acquire_provider_reconciliation_lease(self, **kwargs):
                calls.append(("claim", kwargs["lease_token"]))
            def renew_provider_reconciliation_lease(self, **kwargs):
                calls.append(("renew", kwargs["lease_token"]))
            def release_provider_reconciliation_lease(self, **kwargs):
                calls.append(("release", kwargs["lease_token"]))
            def record_provider_outcome_evidence(self, **kwargs):
                self.claim_token = kwargs["reconciliation_lease_token"]
                return {"outcome": kwargs["evidence"].outcome.value}
        registry = ProviderOutcomeRegistry({"send_email": Adapter()})
        reconciler = ProviderOutcomeReconciler(Store(), registry)
        result = reconciler.reconcile(
            owner_id="owner-1", conversation_id="conversation-1",
            request_id="request-1", call_id="call-1",
        )
        self.assertEqual(result["outcome"], "unknown")
        self.assertEqual(sum(1 for item in calls if isinstance(item, dict)), 1)
        self.assertEqual(next(item for item in calls if isinstance(item, dict))["execution_id"], "execution-1")

    def test_reconciler_renews_claim_during_slow_provider_lookup(self):
        import time
        from core.provider_outcome import ProviderOutcomeReconciler, ProviderOutcomeRegistry
        events = []
        evidence = self.evidence()
        class Adapter:
            provider_id = "mail.example"

            def lookup_tool_outcome(self, execution):
                time.sleep(1.2)
                return evidence
        class Store:
            def list_tool_executions(self, **kwargs):
                return [{
                    "execution_id": "execution-1", "call_id": "call-1",
                    "tool_name": "send_email", "idempotency_key": "a" * 64,
                    "arguments_sha256": "b" * 64, "status": "uncertain",
                }]
            def acquire_provider_reconciliation_lease(self, **kwargs):
                events.append("claim")
            def renew_provider_reconciliation_lease(self, **kwargs):
                events.append("renew")
            def release_provider_reconciliation_lease(self, **kwargs):
                events.append("release")
            def record_provider_outcome_evidence(self, **kwargs):
                events.append("record")
                return {"outcome": "unknown"}
        reconciler = ProviderOutcomeReconciler(
            Store(), ProviderOutcomeRegistry({"send_email": Adapter()}),
            lease_ttl_seconds=5, lease_renew_interval_seconds=1,
        )
        result = reconciler.reconcile(
            owner_id="owner-1", conversation_id="conversation-1",
            request_id="request-1", call_id="call-1",
        )
        self.assertEqual(result["outcome"], "unknown")
        self.assertIn("renew", events)
        self.assertLess(events.index("renew"), events.index("record"))
        self.assertEqual(events[-1], "release")

    def test_reconciler_preserves_lookup_error_when_claim_release_fails(self):
        from core.provider_outcome import ProviderOutcomeReconciler, ProviderOutcomeRegistry
        class Adapter:
            provider_id = "mail.example"

            def lookup_tool_outcome(self, execution):
                raise ValueError("provider lookup failed")
        class Store:
            def list_tool_executions(self, **kwargs):
                return [{
                    "execution_id": "execution-1", "call_id": "call-1",
                    "tool_name": "send_email", "idempotency_key": "a" * 64,
                    "arguments_sha256": "b" * 64, "status": "uncertain",
                }]
            def acquire_provider_reconciliation_lease(self, **kwargs):
                return None
            def renew_provider_reconciliation_lease(self, **kwargs):
                return None
            def release_provider_reconciliation_lease(self, **kwargs):
                raise RuntimeError("release failed")
            def record_provider_outcome_evidence(self, **kwargs):
                raise AssertionError("must not persist evidence")
        reconciler = ProviderOutcomeReconciler(
            Store(), ProviderOutcomeRegistry({"send_email": Adapter()}),
        )
        with self.assertRaisesRegex(ValueError, "provider lookup failed"):
            reconciler.reconcile(
                owner_id="owner-1", conversation_id="conversation-1",
                request_id="request-1", call_id="call-1",
            )

    def test_reconciler_fails_closed_when_lease_heartbeat_is_lost(self):
        import time
        from core.provider_outcome import ProviderOutcomeReconciler, ProviderOutcomeRegistry
        events = []
        class Adapter:
            provider_id = "mail.example"

            def lookup_tool_outcome(self, execution):
                time.sleep(1.2)
                return ProviderOutcomeTests().evidence()
        class Store:
            def list_tool_executions(self, **kwargs):
                return [{
                    "execution_id": "execution-1", "call_id": "call-1",
                    "tool_name": "send_email", "idempotency_key": "a" * 64,
                    "arguments_sha256": "b" * 64, "status": "uncertain",
                }]
            def acquire_provider_reconciliation_lease(self, **kwargs):
                events.append("claim")
            def renew_provider_reconciliation_lease(self, **kwargs):
                events.append("renew")
                raise RuntimeError("lease store unavailable")
            def release_provider_reconciliation_lease(self, **kwargs):
                events.append("release")
            def record_provider_outcome_evidence(self, **kwargs):
                events.append("record")
                raise AssertionError("must not persist after lease loss")
        reconciler = ProviderOutcomeReconciler(
            Store(), ProviderOutcomeRegistry({"send_email": Adapter()}),
            lease_ttl_seconds=5, lease_renew_interval_seconds=1,
        )
        from core.conversation_store import ConversationConflict
        with self.assertRaisesRegex(ConversationConflict, "renewal failed"):
            reconciler.reconcile(
                owner_id="owner-1", conversation_id="conversation-1",
                request_id="request-1", call_id="call-1",
            )
        self.assertIn("renew", events)
        self.assertNotIn("record", events)
        self.assertEqual(events[-1], "release")

    def test_reconciler_cannot_persist_after_another_worker_takes_over_claim(self):
        import time
        from core.conversation_store import ConversationConflict
        from core.provider_outcome import ProviderOutcomeReconciler, ProviderOutcomeRegistry
        events = []
        class Adapter:
            provider_id = "mail.example"

            def lookup_tool_outcome(self, execution):
                time.sleep(1.2)
                return ProviderOutcomeTests().evidence()
        class Store:
            replacement_claimed = False
            def list_tool_executions(self, **kwargs):
                return [{
                    "execution_id": "execution-1", "call_id": "call-1",
                    "tool_name": "send_email", "idempotency_key": "a" * 64,
                    "arguments_sha256": "b" * 64, "status": "uncertain",
                }]
            def acquire_provider_reconciliation_lease(self, **kwargs):
                events.append(("claim", kwargs["lease_token"]))
            def renew_provider_reconciliation_lease(self, **kwargs):
                self.replacement_claimed = True
                events.append(("replacement-claim", "worker-new"))
                raise ConversationConflict("provider reconciliation claim expired or is no longer owned by this worker.")
            def release_provider_reconciliation_lease(self, **kwargs):
                events.append(("release", kwargs["lease_token"]))
                return False
            def record_provider_outcome_evidence(self, **kwargs):
                events.append(("record", kwargs["reconciliation_lease_token"]))
                raise AssertionError("stale worker must not persist evidence")
        store = Store()
        reconciler = ProviderOutcomeReconciler(
            store, ProviderOutcomeRegistry({"send_email": Adapter()}),
            lease_ttl_seconds=5, lease_renew_interval_seconds=1,
        )
        with self.assertRaisesRegex(ConversationConflict, "lease renewal failed"):
            reconciler.reconcile(
                owner_id="owner-1", conversation_id="conversation-1",
                request_id="request-1", call_id="call-1",
            )
        self.assertTrue(store.replacement_claimed)
        self.assertNotIn("record", [event[0] for event in events])
        self.assertEqual(events[-1][0], "release")
        self.assertEqual(events[-1][1], events[0][1])

    def test_registry_does_not_guess_missing_provider_adapter(self):
        from core.provider_outcome import ProviderOutcomeRegistry
        registry = ProviderOutcomeRegistry({})
        with self.assertRaisesRegex(LookupError, "no provider outcome adapter"):
            registry.lookup("send_email", self.execution)

    def test_registry_rejects_async_lookup_before_reconciliation(self):
        from core.provider_outcome import ProviderOutcomeRegistry

        class AsyncAdapter:
            provider_id = "mail.example"

            async def lookup_tool_outcome(self, execution):
                raise AssertionError("async lookup must never be invoked")

        with self.assertRaisesRegex(TypeError, "synchronous lookup"):
            ProviderOutcomeRegistry({"send_email": AsyncAdapter()})

    def test_direct_verification_rejects_async_lookup_before_invocation(self):
        class AsyncAdapter:
            async def lookup_tool_outcome(self, execution):
                raise AssertionError("async lookup must never be invoked")

        with self.assertRaisesRegex(TypeError, "must be synchronous"):
            verify_provider_outcome(AsyncAdapter(), self.execution)

    def test_durable_reconciliation_records_all_outcomes_without_replaying_execution(self):
        import tempfile
        from pathlib import Path
        from core.conversation_store import ConversationStore
        from core.provider_outcome import ProviderOutcomeReconciler, ProviderOutcomeRegistry

        with tempfile.TemporaryDirectory() as directory:
            store = ConversationStore(Path(directory) / "provider-outcomes.sqlite3")
            outcomes = (
                ProviderOutcome.CONFIRMED_COMPLETED,
                ProviderOutcome.CONFIRMED_NOT_APPLIED,
                ProviderOutcome.UNKNOWN,
            )
            for index, outcome in enumerate(outcomes):
                with self.subTest(outcome=outcome.value):
                    conversation = store.create_conversation(owner_id="owner-1")
                    conversation_id = conversation["conversation_id"]
                    request_id = f"reconcile-{index}"
                    store.append_message(
                        owner_id="owner-1", conversation_id=conversation_id,
                        role="user", content="Perform a provider action",
                        idempotency_key=request_id, expected_version=0,
                        generation_request_id=request_id,
                    )
                    generation_token = f"generation-{index}"
                    store.acquire_generation_lease(
                        owner_id="owner-1", conversation_id=conversation_id,
                        lease_token=generation_token, ttl_seconds=60,
                    )
                    try:
                        store.begin_tool_execution(
                            owner_id="owner-1", conversation_id=conversation_id,
                            request_id=request_id, call_id="call-1",
                            tool_name="send_email", arguments_sha256="b" * 64,
                            lease_token=generation_token,
                        )
                        self.assertEqual(
                            store.mark_started_tool_executions_uncertain(
                                owner_id="owner-1", conversation_id=conversation_id,
                                request_id=request_id, lease_token=generation_token,
                            ),
                            1,
                        )
                    finally:
                        store.release_generation_lease(
                            owner_id="owner-1", conversation_id=conversation_id,
                            lease_token=generation_token,
                        )

                    evidence = self.evidence(
                        outcome=outcome,
                        idempotency_key=store.list_tool_executions(
                            owner_id="owner-1", conversation_id=conversation_id,
                            request_id=request_id,
                        )[0]["idempotency_key"],
                    )

                    class Adapter:
                        provider_id = "mail.example"

                        def lookup_tool_outcome(self, execution):
                            return evidence

                    reconciler = ProviderOutcomeReconciler(
                        store, ProviderOutcomeRegistry({"send_email": Adapter()}),
                    )
                    recorded = reconciler.reconcile(
                        owner_id="owner-1", conversation_id=conversation_id,
                        request_id=request_id, call_id="call-1",
                    )
                    self.assertEqual(recorded["outcome"], outcome.value)
                    executions = store.list_tool_executions(
                        owner_id="owner-1", conversation_id=conversation_id,
                        request_id=request_id,
                    )
                    self.assertEqual([item["status"] for item in executions], ["uncertain"])
                    ledger = store.list_provider_outcome_evidence(
                        owner_id="owner-1", conversation_id=conversation_id,
                        request_id=request_id, call_id="call-1",
                    )
                    self.assertEqual(len(ledger), 1)
                    self.assertEqual(ledger[0]["outcome"], outcome.value)

    def test_provider_evidence_digest_cannot_be_rebound_to_another_execution(self):
        import tempfile
        from pathlib import Path
        from core.conversation_store import ConversationConflict, ConversationStore
        from core.provider_outcome import ProviderOutcomeReconciler, ProviderOutcomeRegistry

        with tempfile.TemporaryDirectory() as directory:
            store = ConversationStore(Path(directory) / "evidence-binding.sqlite3")
            shared_key = "d" * 64
            evidence = self.evidence(idempotency_key=shared_key)

            class Adapter:
                provider_id = "mail.example"

                def lookup_tool_outcome(self, execution):
                    return evidence

            reconciler = ProviderOutcomeReconciler(
                store, ProviderOutcomeRegistry({"send_email": Adapter()}),
            )
            conversations = []
            for index in range(2):
                conversation = store.create_conversation(owner_id="owner-1")
                conversation_id = conversation["conversation_id"]
                request_id = f"evidence-binding-{index}"
                store.append_message(
                    owner_id="owner-1", conversation_id=conversation_id,
                    role="user", content="Perform provider action",
                    idempotency_key=request_id, expected_version=0,
                    generation_request_id=request_id,
                )
                token = f"generation-{index}"
                store.acquire_generation_lease(
                    owner_id="owner-1", conversation_id=conversation_id,
                    lease_token=token, ttl_seconds=60,
                )
                try:
                    store.begin_tool_execution(
                        owner_id="owner-1", conversation_id=conversation_id,
                        request_id=request_id, call_id="call-1",
                        tool_name="send_email", arguments_sha256="b" * 64,
                        idempotency_key=shared_key, lease_token=token,
                    )
                    store.mark_started_tool_executions_uncertain(
                        owner_id="owner-1", conversation_id=conversation_id,
                        request_id=request_id, lease_token=token,
                    )
                finally:
                    store.release_generation_lease(
                        owner_id="owner-1", conversation_id=conversation_id,
                        lease_token=token,
                    )
                conversations.append((conversation_id, request_id))

            first_conversation, first_request = conversations[0]
            reconciler.reconcile(
                owner_id="owner-1", conversation_id=first_conversation,
                request_id=first_request, call_id="call-1",
            )
            second_conversation, second_request = conversations[1]
            with self.assertRaisesRegex(ConversationConflict, "another execution"):
                reconciler.reconcile(
                    owner_id="owner-1", conversation_id=second_conversation,
                    request_id=second_request, call_id="call-1",
                )
            self.assertEqual(
                len(store.list_provider_outcome_evidence(
                    owner_id="owner-1", conversation_id=first_conversation,
                    request_id=first_request, call_id="call-1",
                )),
                1,
            )
            self.assertEqual(
                store.list_provider_outcome_evidence(
                    owner_id="owner-1", conversation_id=second_conversation,
                    request_id=second_request, call_id="call-1",
                ),
                [],
            )

    def test_conflicting_provider_observations_cannot_be_overridden_by_later_success(self):
        import tempfile
        from pathlib import Path
        from core.conversation_store import ConversationConflict, ConversationStore
        from core.provider_outcome import ProviderOutcomeReconciler, ProviderOutcomeRegistry

        with tempfile.TemporaryDirectory() as directory:
            store = ConversationStore(Path(directory) / "conflicting-evidence.sqlite3")
            conversation = store.create_conversation(owner_id="owner-1")
            conversation_id = conversation["conversation_id"]
            request_id = "conflicting-provider-observations"
            store.append_message(
                owner_id="owner-1", conversation_id=conversation_id,
                role="user", content="Perform provider action",
                idempotency_key=request_id, expected_version=0,
                generation_request_id=request_id,
            )
            token = "generation-conflicting-evidence"
            store.acquire_generation_lease(
                owner_id="owner-1", conversation_id=conversation_id,
                lease_token=token, ttl_seconds=60,
            )
            try:
                execution = store.begin_tool_execution(
                    owner_id="owner-1", conversation_id=conversation_id,
                    request_id=request_id, call_id="call-1",
                    tool_name="send_email", arguments_sha256="b" * 64,
                    lease_token=token, provider_idempotency=True,
                )
                store.mark_started_tool_executions_uncertain(
                    owner_id="owner-1", conversation_id=conversation_id,
                    request_id=request_id, lease_token=token,
                )
            finally:
                store.release_generation_lease(
                    owner_id="owner-1", conversation_id=conversation_id,
                    lease_token=token,
                )

            observations = [
                self.evidence(
                    outcome=ProviderOutcome.UNKNOWN,
                    evidence_sha256="c" * 64,
                    idempotency_key=execution["idempotency_key"],
                ),
                self.evidence(
                    outcome=ProviderOutcome.CONFIRMED_COMPLETED,
                    evidence_sha256="e" * 64,
                    idempotency_key=execution["idempotency_key"],
                ),
            ]

            class Adapter:
                provider_id = "mail.example"

                def lookup_tool_outcome(self, execution):
                    return observations.pop(0)

            reconciler = ProviderOutcomeReconciler(
                store, ProviderOutcomeRegistry({"send_email": Adapter()}),
            )
            for _ in range(2):
                reconciler.reconcile(
                    owner_id="owner-1", conversation_id=conversation_id,
                    request_id=request_id, call_id="call-1",
                )

            ledger = store.list_provider_outcome_evidence(
                owner_id="owner-1", conversation_id=conversation_id,
                request_id=request_id, call_id="call-1",
            )
            self.assertEqual(
                {item["outcome"] for item in ledger},
                {"unknown", "confirmed_completed"},
            )
            with self.assertRaisesRegex(ConversationConflict, "unanimous"):
                store.review_tool_execution(
                    owner_id="owner-1", conversation_id=conversation_id,
                    request_id=request_id, call_id="call-1",
                    action="confirm_provider_completed",
                    reason="A later lookup reported success, but an earlier observation remains unknown.",
                )
            execution_rows = store.list_tool_executions(
                owner_id="owner-1", conversation_id=conversation_id,
                request_id=request_id,
            )
            self.assertEqual(execution_rows[0]["status"], "uncertain")

    def test_provider_identity_cannot_change_between_reconciliation_attempts(self):
        import tempfile
        from pathlib import Path
        from core.conversation_store import ConversationConflict, ConversationStore
        from core.provider_outcome import ProviderOutcomeReconciler, ProviderOutcomeRegistry

        with tempfile.TemporaryDirectory() as directory:
            store = ConversationStore(Path(directory) / "provider-identity.sqlite3")
            conversation = store.create_conversation(owner_id="owner-1")
            conversation_id = conversation["conversation_id"]
            request_id = "provider-identity-pinning"
            store.append_message(
                owner_id="owner-1", conversation_id=conversation_id,
                role="user", content="Perform provider action",
                idempotency_key=request_id, expected_version=0,
                generation_request_id=request_id,
            )
            token = "generation-provider-identity"
            store.acquire_generation_lease(
                owner_id="owner-1", conversation_id=conversation_id,
                lease_token=token, ttl_seconds=60,
            )
            try:
                execution = store.begin_tool_execution(
                    owner_id="owner-1", conversation_id=conversation_id,
                    request_id=request_id, call_id="call-1",
                    tool_name="send_email", arguments_sha256="b" * 64,
                    lease_token=token,
                )
                store.mark_started_tool_executions_uncertain(
                    owner_id="owner-1", conversation_id=conversation_id,
                    request_id=request_id, lease_token=token,
                )
            finally:
                store.release_generation_lease(
                    owner_id="owner-1", conversation_id=conversation_id,
                    lease_token=token,
                )

            class FirstAdapter:
                provider_id = "mail.example"

                def lookup_tool_outcome(self, identity):
                    return ProviderOutcomeTests().evidence(
                        idempotency_key=identity["idempotency_key"],
                        arguments_sha256=identity["arguments_sha256"],
                    )

            class ReplacementAdapter:
                provider_id = "mail-replacement.example"

                def lookup_tool_outcome(self, identity):
                    return ProviderOutcomeTests().evidence(
                        provider="mail-replacement.example",
                        idempotency_key=identity["idempotency_key"],
                        arguments_sha256=identity["arguments_sha256"],
                        evidence_sha256="e" * 64,
                    )

            first = ProviderOutcomeReconciler(
                store, ProviderOutcomeRegistry({"send_email": FirstAdapter()}),
            )
            first.reconcile(
                owner_id="owner-1", conversation_id=conversation_id,
                request_id=request_id, call_id="call-1",
            )
            replacement = ProviderOutcomeReconciler(
                store, ProviderOutcomeRegistry({"send_email": ReplacementAdapter()}),
            )
            with self.assertRaisesRegex(ConversationConflict, "provider identity cannot change"):
                replacement.reconcile(
                    owner_id="owner-1", conversation_id=conversation_id,
                    request_id=request_id, call_id="call-1",
                )
            ledger = store.list_provider_outcome_evidence(
                owner_id="owner-1", conversation_id=conversation_id,
                request_id=request_id, call_id="call-1",
            )
            self.assertEqual(len(ledger), 1)
            self.assertEqual(ledger[0]["provider"], "mail.example")

    def test_sync_adapter_cannot_smuggle_awaitable_lookup_result(self):
        async def deferred_evidence():
            return ProviderOutcomeTests().evidence()

        class Adapter:
            def lookup_tool_outcome(self, execution):
                return deferred_evidence()

        with self.assertRaisesRegex(TypeError, "returned an awaitable"):
            verify_provider_outcome(Adapter(), self.execution)

if __name__ == "__main__":
    unittest.main()
