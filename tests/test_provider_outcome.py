import unittest
from datetime import datetime, timezone

from core.provider_outcome import (
    ProviderOutcome, ProviderOutcomeEvidence, verify_provider_outcome,
)


def _multiprocess_claim_provider_reconciliation_lease(database_path, conversation_id, request_id, token, barrier, results):
    from core.conversation_store import ConversationConflict, ConversationStore
    store = ConversationStore(database_path)
    try:
        barrier.wait(timeout=10)
        store.acquire_provider_reconciliation_lease(
            owner_id="owner-process", conversation_id=conversation_id,
            request_id=request_id, call_id="call-process",
            lease_token=token, ttl_seconds=30,
        )
    except ConversationConflict:
        results.put(("lost", token))
    except Exception as exc:
        results.put(("error", repr(exc)))
    else:
        results.put(("won", token))


def _multiprocess_provider_claim_then_crash(database_path, conversation_id, request_id, connection):
    import os
    import core.conversation_store as store_module
    from core.conversation_store import ConversationStore

    store_module.time.time = lambda: 100.0
    store = ConversationStore(database_path)
    store.acquire_provider_reconciliation_lease(
        owner_id="owner-crash", conversation_id=conversation_id,
        request_id=request_id, call_id="call-crash",
        lease_token="crashed-worker", ttl_seconds=5,
    )
    connection.send("claim-acquired")
    connection.close()
    os._exit(0)



def _multiprocess_reconcile_and_crash_before_evidence(database_path, conversation_id, request_id, connection):
    import os
    import core.conversation_store as store_module
    from core.conversation_store import ConversationStore
    from core.provider_outcome import (
        ProviderOutcome, ProviderOutcomeEvidence, ProviderOutcomeReconciler,
        ProviderOutcomeRegistry,
    )

    store_module.time.time = lambda: 100.0
    store = ConversationStore(database_path)

    class Adapter:
        provider_id = "mail.example"

        def lookup_tool_outcome(self, identity):
            return ProviderOutcomeEvidence(
                provider="mail.example", outcome=ProviderOutcome.UNKNOWN,
                idempotency_key=identity["idempotency_key"],
                arguments_sha256=identity["arguments_sha256"],
                evidence_sha256="d" * 64,
                observed_at=datetime.now(timezone.utc).isoformat(),
                execution_id=identity["execution_id"],
            )

    def crash_before_persist(**kwargs):
        connection.send("provider-lookup-returned")
        connection.close()
        os._exit(0)

    store.record_provider_outcome_evidence = crash_before_persist
    reconciler = ProviderOutcomeReconciler(
        store, ProviderOutcomeRegistry({"send_email": Adapter()}),
        lease_ttl_seconds=5, lease_renew_interval_seconds=1,
    )
    reconciler.reconcile(
        owner_id="owner-reconcile-crash", conversation_id=conversation_id,
        request_id=request_id, call_id="call-reconcile-crash",
    )
    os._exit(2)


def _multiprocess_reconcile_after_crash(database_path, conversation_id, request_id, barrier, connection, lookup_count, lookup_lock):
    import core.conversation_store as store_module
    from core.conversation_store import ConversationConflict, ConversationStore
    from core.provider_outcome import (
        ProviderOutcome, ProviderOutcomeEvidence, ProviderOutcomeReconciler,
        ProviderOutcomeRegistry,
    )
    store_module.time.time = lambda: 106.0
    store = ConversationStore(database_path)

    class Adapter:
        provider_id = "mail.example"
        def lookup_tool_outcome(self, identity):
            with lookup_lock:
                lookup_count.value += 1
            return ProviderOutcomeEvidence(
                provider="mail.example", outcome=ProviderOutcome.UNKNOWN,
                idempotency_key=identity["idempotency_key"],
                arguments_sha256=identity["arguments_sha256"],
                evidence_sha256="f" * 64,
                observed_at=datetime.now(timezone.utc).isoformat(),
                execution_id=identity["execution_id"],
            )

    reconciler = ProviderOutcomeReconciler(
        store, ProviderOutcomeRegistry({"send_email": Adapter()}),
        lease_ttl_seconds=5, lease_renew_interval_seconds=1,
    )
    try:
        barrier.wait(timeout=15)
        result = reconciler.reconcile(
            owner_id="owner-reconcile-crash", conversation_id=conversation_id,
            request_id=request_id, call_id="call-reconcile-crash",
        )
    except ConversationConflict:
        connection.send(("lost", None))
    except Exception as exc:
        connection.send(("error", repr(exc)))
    else:
        connection.send(("won", result["evidence_sha256"]))
    finally:
        connection.close()

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
            "execution_id": "execution-1",
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

    def test_evidence_for_different_execution_is_rejected_even_when_fingerprints_match(self):
        class Adapter:
            def lookup_tool_outcome(self, execution):
                return ProviderOutcomeTests().evidence(
                    execution_id="another-execution",
                    idempotency_key=execution["idempotency_key"],
                    arguments_sha256=execution["arguments_sha256"],
                )

        with self.assertRaisesRegex(ValueError, "exact durable execution identity"):
            verify_provider_outcome(Adapter(), self.execution)

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
                        execution_id=store.list_tool_executions(
                            owner_id="owner-1", conversation_id=conversation_id,
                            request_id=request_id,
                        )[0]["execution_id"],
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
            first_execution_id = None

            class Adapter:
                provider_id = "mail.example"

                def lookup_tool_outcome(self, execution):
                    nonlocal first_execution_id
                    if first_execution_id is None:
                        first_execution_id = execution["execution_id"]
                    return ProviderOutcomeTests().evidence(
                        execution_id=first_execution_id,
                        idempotency_key=shared_key,
                    )

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
                    execution_id=execution["execution_id"],
                ),
                self.evidence(
                    outcome=ProviderOutcome.CONFIRMED_COMPLETED,
                    evidence_sha256="e" * 64,
                    idempotency_key=execution["idempotency_key"],
                    execution_id=execution["execution_id"],
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
                        execution_id=identity["execution_id"],
                        idempotency_key=identity["idempotency_key"],
                        arguments_sha256=identity["arguments_sha256"],
                    )

            class ReplacementAdapter:
                provider_id = "mail-replacement.example"

                def lookup_tool_outcome(self, identity):
                    return ProviderOutcomeTests().evidence(
                        execution_id=identity["execution_id"],
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

    def test_cross_instance_reconciliation_lease_allows_only_one_concurrent_worker(self):
        import tempfile
        import threading
        from concurrent.futures import ThreadPoolExecutor
        from pathlib import Path
        from core.conversation_store import ConversationConflict, ConversationStore

        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "reconciliation-race.sqlite3"
            setup = ConversationStore(database)
            conversation = setup.create_conversation(owner_id="owner-race")
            conversation_id = conversation["conversation_id"]
            request_id = "cross-instance-reconciliation-race"
            setup.append_message(
                owner_id="owner-race", conversation_id=conversation_id,
                role="user", content="Check provider execution",
                idempotency_key=request_id, expected_version=0,
                generation_request_id=request_id,
            )
            generation_token = "generation-race"
            setup.acquire_generation_lease(
                owner_id="owner-race", conversation_id=conversation_id,
                lease_token=generation_token, ttl_seconds=60,
            )
            try:
                setup.begin_tool_execution(
                    owner_id="owner-race", conversation_id=conversation_id,
                    request_id=request_id, call_id="call-race",
                    tool_name="send_email", arguments_sha256="b" * 64,
                    lease_token=generation_token,
                )
                setup.mark_started_tool_executions_uncertain(
                    owner_id="owner-race", conversation_id=conversation_id,
                    request_id=request_id, lease_token=generation_token,
                )
            finally:
                setup.release_generation_lease(
                    owner_id="owner-race", conversation_id=conversation_id,
                    lease_token=generation_token,
                )

            stores = (ConversationStore(database), ConversationStore(database))
            barrier = threading.Barrier(2)

            def claim(index):
                barrier.wait(timeout=5)
                try:
                    stores[index].acquire_provider_reconciliation_lease(
                        owner_id="owner-race", conversation_id=conversation_id,
                        request_id=request_id, call_id="call-race",
                        lease_token=f"worker-{index}", ttl_seconds=30,
                    )
                    return "won"
                except ConversationConflict:
                    return "lost"

            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(claim, range(2)))
            self.assertEqual(results.count("won"), 1)
            self.assertEqual(results.count("lost"), 1)

    def test_multiprocess_reconciliation_claim_is_exclusive(self):
        import multiprocessing
        import tempfile
        from pathlib import Path
        from core.conversation_store import ConversationStore

        context = multiprocessing.get_context("spawn")
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "multiprocess-reconciliation.sqlite3"
            store = ConversationStore(database)
            conversation = store.create_conversation(owner_id="owner-process")
            conversation_id = conversation["conversation_id"]
            request_id = "multiprocess-provider-claim"
            store.append_message(
                owner_id="owner-process", conversation_id=conversation_id,
                role="user", content="Check provider execution",
                idempotency_key=request_id, expected_version=0,
                generation_request_id=request_id,
            )
            generation_token = "generation-process"
            store.acquire_generation_lease(
                owner_id="owner-process", conversation_id=conversation_id,
                lease_token=generation_token, ttl_seconds=60,
            )
            try:
                store.begin_tool_execution(
                    owner_id="owner-process", conversation_id=conversation_id,
                    request_id=request_id, call_id="call-process",
                    tool_name="send_email", arguments_sha256="b" * 64,
                    lease_token=generation_token,
                )
                store.mark_started_tool_executions_uncertain(
                    owner_id="owner-process", conversation_id=conversation_id,
                    request_id=request_id, lease_token=generation_token,
                )
            finally:
                store.release_generation_lease(
                    owner_id="owner-process", conversation_id=conversation_id,
                    lease_token=generation_token,
                )

            barrier = context.Barrier(2)
            results = context.Queue()
            workers = [
                context.Process(
                    target=_multiprocess_claim_provider_reconciliation_lease,
                    args=(str(database), conversation_id, request_id,
                          f"process-worker-{index}", barrier, results),
                )
                for index in range(2)
            ]
            for worker in workers:
                worker.start()
            outcomes = [results.get(timeout=15) for _ in workers]
            for worker in workers:
                worker.join(timeout=15)
                self.assertEqual(worker.exitcode, 0)
            self.assertEqual([item[0] for item in outcomes].count("won"), 1)
            self.assertEqual([item[0] for item in outcomes].count("lost"), 1)
            self.assertFalse(any(item[0] == "error" for item in outcomes))

    def test_expired_reconciliation_worker_is_fenced_after_claim_takeover(self):
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        from core.conversation_store import ConversationConflict, ConversationStore

        with tempfile.TemporaryDirectory() as directory:
            store = ConversationStore(Path(directory) / "expired-provider-claim.sqlite3")
            conversation = store.create_conversation(owner_id="owner-fence")
            conversation_id = conversation["conversation_id"]
            request_id = "expired-provider-claim"
            store.append_message(
                owner_id="owner-fence", conversation_id=conversation_id,
                role="user", content="Check provider execution",
                idempotency_key=request_id, expected_version=0,
                generation_request_id=request_id,
            )
            generation_token = "generation-fence"
            store.acquire_generation_lease(
                owner_id="owner-fence", conversation_id=conversation_id,
                lease_token=generation_token, ttl_seconds=60,
            )
            try:
                execution = store.begin_tool_execution(
                    owner_id="owner-fence", conversation_id=conversation_id,
                    request_id=request_id, call_id="call-fence",
                    tool_name="send_email", arguments_sha256="b" * 64,
                    lease_token=generation_token,
                )
                store.mark_started_tool_executions_uncertain(
                    owner_id="owner-fence", conversation_id=conversation_id,
                    request_id=request_id, lease_token=generation_token,
                )
            finally:
                store.release_generation_lease(
                    owner_id="owner-fence", conversation_id=conversation_id,
                    lease_token=generation_token,
                )

            stale_evidence = self.evidence(
                execution_id=execution["execution_id"],
                idempotency_key=execution["idempotency_key"],
                arguments_sha256=execution["arguments_sha256"],
                evidence_sha256="d" * 64,
            )
            replacement_evidence = self.evidence(
                execution_id=execution["execution_id"],
                idempotency_key=execution["idempotency_key"],
                arguments_sha256=execution["arguments_sha256"],
                evidence_sha256="e" * 64,
            )
            with patch("core.conversation_store.time.time", side_effect=[100.0, 106.0, 106.0]):
                store.acquire_provider_reconciliation_lease(
                    owner_id="owner-fence", conversation_id=conversation_id,
                    request_id=request_id, call_id="call-fence",
                    lease_token="expired-worker", ttl_seconds=5,
                )
                store.acquire_provider_reconciliation_lease(
                    owner_id="owner-fence", conversation_id=conversation_id,
                    request_id=request_id, call_id="call-fence",
                    lease_token="replacement-worker", ttl_seconds=30,
                )
                with self.assertRaisesRegex(ConversationConflict, "expired or is no longer owned"):
                    store.record_provider_outcome_evidence(
                        owner_id="owner-fence", conversation_id=conversation_id,
                        request_id=request_id, call_id="call-fence",
                        evidence=stale_evidence,
                        reconciliation_lease_token="expired-worker",
                    )
                self.assertFalse(store.release_provider_reconciliation_lease(
                    owner_id="owner-fence", execution_id=execution["execution_id"],
                    lease_token="expired-worker",
                ))
                recorded = store.record_provider_outcome_evidence(
                    owner_id="owner-fence", conversation_id=conversation_id,
                    request_id=request_id, call_id="call-fence",
                    evidence=replacement_evidence,
                    reconciliation_lease_token="replacement-worker",
                )
                self.assertEqual(recorded["evidence_sha256"], "e" * 64)
                self.assertTrue(store.release_provider_reconciliation_lease(
                    owner_id="owner-fence", execution_id=execution["execution_id"],
                    lease_token="replacement-worker",
                ))
            ledger = store.list_provider_outcome_evidence(
                owner_id="owner-fence", conversation_id=conversation_id,
                request_id=request_id, call_id="call-fence",
            )
            self.assertEqual(len(ledger), 1)
            self.assertEqual(ledger[0]["evidence_sha256"], "e" * 64)

    def test_process_crash_releases_reconciliation_after_lease_expiry(self):
        import multiprocessing
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        from core.conversation_store import ConversationStore

        context = multiprocessing.get_context("spawn")
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "provider-claim-crash.sqlite3"
            store = ConversationStore(database)
            conversation = store.create_conversation(owner_id="owner-crash")
            conversation_id = conversation["conversation_id"]
            request_id = "provider-claim-process-crash"
            store.append_message(
                owner_id="owner-crash", conversation_id=conversation_id,
                role="user", content="Check provider execution",
                idempotency_key=request_id, expected_version=0,
                generation_request_id=request_id,
            )
            generation_token = "generation-crash"
            store.acquire_generation_lease(
                owner_id="owner-crash", conversation_id=conversation_id,
                lease_token=generation_token, ttl_seconds=60,
            )
            try:
                execution = store.begin_tool_execution(
                    owner_id="owner-crash", conversation_id=conversation_id,
                    request_id=request_id, call_id="call-crash",
                    tool_name="send_email", arguments_sha256="b" * 64,
                    lease_token=generation_token,
                )
                store.mark_started_tool_executions_uncertain(
                    owner_id="owner-crash", conversation_id=conversation_id,
                    request_id=request_id, lease_token=generation_token,
                )
            finally:
                store.release_generation_lease(
                    owner_id="owner-crash", conversation_id=conversation_id,
                    lease_token=generation_token,
                )

            parent, child = context.Pipe(duplex=False)
            worker = context.Process(
                target=_multiprocess_provider_claim_then_crash,
                args=(str(database), conversation_id, request_id, child),
            )
            worker.start()
            child.close()
            self.assertEqual(parent.recv(), "claim-acquired")
            parent.close()
            worker.join(timeout=15)
            self.assertEqual(worker.exitcode, 0)

            evidence = self.evidence(
                execution_id=execution["execution_id"],
                idempotency_key=execution["idempotency_key"],
                arguments_sha256=execution["arguments_sha256"],
                evidence_sha256="f" * 64,
            )
            with patch("core.conversation_store.time.time", return_value=106.0):
                store.acquire_provider_reconciliation_lease(
                    owner_id="owner-crash", conversation_id=conversation_id,
                    request_id=request_id, call_id="call-crash",
                    lease_token="recovery-worker", ttl_seconds=30,
                )
                recorded = store.record_provider_outcome_evidence(
                    owner_id="owner-crash", conversation_id=conversation_id,
                    request_id=request_id, call_id="call-crash",
                    evidence=evidence,
                    reconciliation_lease_token="recovery-worker",
                )
                self.assertEqual(recorded["evidence_sha256"], "f" * 64)
                self.assertTrue(store.release_provider_reconciliation_lease(
                    owner_id="owner-crash", execution_id=execution["execution_id"],
                    lease_token="recovery-worker",
                ))
            ledger = store.list_provider_outcome_evidence(
                owner_id="owner-crash", conversation_id=conversation_id,
                request_id=request_id, call_id="call-crash",
            )
            self.assertEqual(len(ledger), 1)
            self.assertEqual(ledger[0]["provider"], "mail.example")

    def test_process_crash_after_provider_lookup_repeats_read_only_lookup_without_replaying_tool(self):
        import multiprocessing
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        from core.conversation_store import ConversationStore
        from core.provider_outcome import ProviderOutcomeReconciler, ProviderOutcomeRegistry

        context = multiprocessing.get_context("spawn")
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "provider-lookup-before-persist-crash.sqlite3"
            store = ConversationStore(database)
            conversation = store.create_conversation(owner_id="owner-reconcile-crash")
            conversation_id = conversation["conversation_id"]
            request_id = "provider-lookup-before-persist-crash"
            store.append_message(
                owner_id="owner-reconcile-crash", conversation_id=conversation_id,
                role="user", content="Check the uncertain provider action",
                idempotency_key=request_id, expected_version=0,
                generation_request_id=request_id,
            )
            generation_token = "generation-reconcile-crash"
            store.acquire_generation_lease(
                owner_id="owner-reconcile-crash", conversation_id=conversation_id,
                lease_token=generation_token, ttl_seconds=60,
            )
            try:
                execution = store.begin_tool_execution(
                    owner_id="owner-reconcile-crash", conversation_id=conversation_id,
                    request_id=request_id, call_id="call-reconcile-crash",
                    tool_name="send_email", arguments_sha256="b" * 64,
                    lease_token=generation_token,
                )
                store.mark_started_tool_executions_uncertain(
                    owner_id="owner-reconcile-crash", conversation_id=conversation_id,
                    request_id=request_id, lease_token=generation_token,
                )
            finally:
                store.release_generation_lease(
                    owner_id="owner-reconcile-crash", conversation_id=conversation_id,
                    lease_token=generation_token,
                )

            parent, child = context.Pipe(duplex=False)
            worker = context.Process(
                target=_multiprocess_reconcile_and_crash_before_evidence,
                args=(str(database), conversation_id, request_id, child),
            )
            worker.start()
            child.close()
            self.assertEqual(parent.recv(), "provider-lookup-returned")
            parent.close()
            worker.join(timeout=15)
            self.assertEqual(worker.exitcode, 0)
            self.assertEqual(store.list_provider_outcome_evidence(
                owner_id="owner-reconcile-crash", conversation_id=conversation_id,
                request_id=request_id, call_id="call-reconcile-crash",
            ), [])

            replacement_lookups = []

            class ReplacementAdapter:
                provider_id = "mail.example"

                def lookup_tool_outcome(self, identity):
                    replacement_lookups.append(identity["execution_id"])
                    return ProviderOutcomeTests().evidence(
                        execution_id=identity["execution_id"],
                        idempotency_key=identity["idempotency_key"],
                        arguments_sha256=identity["arguments_sha256"],
                        evidence_sha256="e" * 64,
                    )

            reconciler = ProviderOutcomeReconciler(
                store, ProviderOutcomeRegistry({"send_email": ReplacementAdapter()}),
                lease_ttl_seconds=5, lease_renew_interval_seconds=1,
            )
            with patch("core.conversation_store.time.time", return_value=106.0):
                result = reconciler.reconcile(
                    owner_id="owner-reconcile-crash", conversation_id=conversation_id,
                    request_id=request_id, call_id="call-reconcile-crash",
                )
            self.assertEqual(result["evidence_sha256"], "e" * 64)
            self.assertEqual(replacement_lookups, [execution["execution_id"]])
            self.assertEqual(execution["status"], "started")
            ledger = store.list_provider_outcome_evidence(
                owner_id="owner-reconcile-crash", conversation_id=conversation_id,
                request_id=request_id, call_id="call-reconcile-crash",
            )
            self.assertEqual(len(ledger), 1)
            self.assertEqual(ledger[0]["execution_id"], execution["execution_id"])
            self.assertEqual(ledger[0]["evidence_sha256"], "e" * 64)
            self.assertEqual(store.list_tool_executions(
                owner_id="owner-reconcile-crash", conversation_id=conversation_id,
                request_id=request_id,
            )[0]["status"], "uncertain")


    def test_concurrent_replacement_processes_allow_only_one_post_crash_observation(self):
        import multiprocessing
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        from core.conversation_store import ConversationStore

        context = multiprocessing.get_context("spawn")
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "concurrent-provider-recovery.sqlite3"
            store = ConversationStore(database)
            conversation = store.create_conversation(owner_id="owner-reconcile-crash")
            conversation_id = conversation["conversation_id"]
            request_id = "concurrent-provider-recovery"
            store.append_message(
                owner_id="owner-reconcile-crash", conversation_id=conversation_id,
                role="user", content="Recover provider status",
                idempotency_key=request_id, expected_version=0,
                generation_request_id=request_id,
            )
            generation_token = "generation-concurrent-recovery"
            store.acquire_generation_lease(
                owner_id="owner-reconcile-crash", conversation_id=conversation_id,
                lease_token=generation_token, ttl_seconds=60,
            )
            try:
                execution = store.begin_tool_execution(
                    owner_id="owner-reconcile-crash", conversation_id=conversation_id,
                    request_id=request_id, call_id="call-reconcile-crash",
                    tool_name="send_email", arguments_sha256="b" * 64,
                    lease_token=generation_token,
                )
                store.mark_started_tool_executions_uncertain(
                    owner_id="owner-reconcile-crash", conversation_id=conversation_id,
                    request_id=request_id, lease_token=generation_token,
                )
            finally:
                store.release_generation_lease(
                    owner_id="owner-reconcile-crash", conversation_id=conversation_id,
                    lease_token=generation_token,
                )

            with patch("core.conversation_store.time.time", return_value=100.0):
                store.acquire_provider_reconciliation_lease(
                    owner_id="owner-reconcile-crash", conversation_id=conversation_id,
                    request_id=request_id, call_id="call-reconcile-crash",
                    lease_token="crashed-worker", ttl_seconds=5,
                )

            barrier = context.Barrier(2)
            lookup_count = context.Value("i", 0)
            lookup_lock = context.Lock()
            workers, parents = [], []
            for _ in range(2):
                parent, child = context.Pipe(duplex=False)
                parents.append(parent)
                worker = context.Process(
                    target=_multiprocess_reconcile_after_crash,
                    args=(str(database), conversation_id, request_id, barrier,
                          child, lookup_count, lookup_lock),
                )
                workers.append(worker)
                worker.start()
                child.close()

            outcomes = [parent.recv() for parent in parents]
            for parent in parents:
                parent.close()
            for worker in workers:
                worker.join(timeout=20)
                self.assertEqual(worker.exitcode, 0)

            self.assertEqual(sorted(outcome[0] for outcome in outcomes), ["lost", "won"])
            self.assertEqual(lookup_count.value, 1)
            ledger = store.list_provider_outcome_evidence(
                owner_id="owner-reconcile-crash", conversation_id=conversation_id,
                request_id=request_id, call_id="call-reconcile-crash",
            )
            self.assertEqual(len(ledger), 1)
            self.assertEqual(ledger[0]["execution_id"], execution["execution_id"])
            self.assertEqual(ledger[0]["evidence_sha256"], "f" * 64)
            self.assertEqual(store.list_tool_executions(
                owner_id="owner-reconcile-crash", conversation_id=conversation_id,
                request_id=request_id,
            )[0]["status"], "uncertain")

    def test_reconciliation_heartbeat_keeps_slow_provider_lookup_claimed(self):
        import tempfile
        import threading
        from pathlib import Path
        from core.conversation_store import ConversationConflict, ConversationStore
        from core.provider_outcome import ProviderOutcomeReconciler, ProviderOutcomeRegistry

        with tempfile.TemporaryDirectory() as directory:
            store = ConversationStore(Path(directory) / "slow-provider-lookup.sqlite3")
            conversation = store.create_conversation(owner_id="owner-heartbeat")
            conversation_id = conversation["conversation_id"]
            request_id = "slow-provider-lookup"
            store.append_message(
                owner_id="owner-heartbeat", conversation_id=conversation_id,
                role="user", content="Check provider execution",
                idempotency_key=request_id, expected_version=0,
                generation_request_id=request_id,
            )
            generation_token = "generation-heartbeat"
            store.acquire_generation_lease(
                owner_id="owner-heartbeat", conversation_id=conversation_id,
                lease_token=generation_token, ttl_seconds=60,
            )
            try:
                execution = store.begin_tool_execution(
                    owner_id="owner-heartbeat", conversation_id=conversation_id,
                    request_id=request_id, call_id="call-heartbeat",
                    tool_name="send_email", arguments_sha256="b" * 64,
                    lease_token=generation_token,
                )
                store.mark_started_tool_executions_uncertain(
                    owner_id="owner-heartbeat", conversation_id=conversation_id,
                    request_id=request_id, lease_token=generation_token,
                )
            finally:
                store.release_generation_lease(
                    owner_id="owner-heartbeat", conversation_id=conversation_id,
                    lease_token=generation_token,
                )

            lookup_started = threading.Event()
            finish_lookup = threading.Event()
            renewal_observed = threading.Event()
            original_renew = store.renew_provider_reconciliation_lease

            def observe_renewal(**kwargs):
                result = original_renew(**kwargs)
                renewal_observed.set()
                return result

            store.renew_provider_reconciliation_lease = observe_renewal

            class SlowAdapter:
                provider_id = "mail.example"

                def lookup_tool_outcome(self, identity):
                    self.asserted_identity = identity
                    lookup_started.set()
                    if not finish_lookup.wait(10):
                        raise TimeoutError("test did not release slow lookup")
                    return ProviderOutcomeTests().evidence(
                        execution_id=execution["execution_id"],
                        idempotency_key=execution["idempotency_key"],
                        arguments_sha256=execution["arguments_sha256"],
                    )

            reconciler = ProviderOutcomeReconciler(
                store, ProviderOutcomeRegistry({"send_email": SlowAdapter()}),
                lease_ttl_seconds=5, lease_renew_interval_seconds=1,
            )
            outcomes = []

            def run_reconciliation():
                try:
                    outcomes.append(reconciler.reconcile(
                        owner_id="owner-heartbeat", conversation_id=conversation_id,
                        request_id=request_id, call_id="call-heartbeat",
                    ))
                except Exception as exc:
                    outcomes.append(exc)

            worker = threading.Thread(target=run_reconciliation, daemon=True)
            worker.start()
            try:
                self.assertTrue(lookup_started.wait(5), "provider lookup did not start")
                self.assertTrue(renewal_observed.wait(5), "lease heartbeat did not renew")
                with self.assertRaises(ConversationConflict):
                    store.acquire_provider_reconciliation_lease(
                        owner_id="owner-heartbeat", conversation_id=conversation_id,
                        request_id=request_id, call_id="call-heartbeat",
                        lease_token="competing-worker", ttl_seconds=30,
                    )
            finally:
                finish_lookup.set()
                worker.join(timeout=10)
            self.assertFalse(worker.is_alive(), "reconciliation worker did not finish")
            self.assertEqual(len(outcomes), 1)
            self.assertIsInstance(outcomes[0], dict)
            ledger = store.list_provider_outcome_evidence(
                owner_id="owner-heartbeat", conversation_id=conversation_id,
                request_id=request_id, call_id="call-heartbeat",
            )
            self.assertEqual(len(ledger), 1)
            self.assertEqual(ledger[0]["execution_id"], execution["execution_id"])

    def test_real_store_does_not_persist_evidence_after_slow_lookup_heartbeat_failure(self):
        import tempfile
        import threading
        from pathlib import Path
        from unittest.mock import patch
        from core.conversation_store import ConversationConflict, ConversationStore
        from core.provider_outcome import ProviderOutcomeReconciler, ProviderOutcomeRegistry

        with tempfile.TemporaryDirectory() as directory:
            store = ConversationStore(Path(directory) / "heartbeat-loss.sqlite3")
            conversation = store.create_conversation(owner_id="owner-heartbeat-loss")
            conversation_id = conversation["conversation_id"]
            request_id = "heartbeat-loss-request"
            store.append_message(
                owner_id="owner-heartbeat-loss", conversation_id=conversation_id,
                role="user", content="Check provider execution",
                idempotency_key=request_id, expected_version=0,
                generation_request_id=request_id,
            )
            generation_token = "generation-heartbeat-loss"
            store.acquire_generation_lease(
                owner_id="owner-heartbeat-loss", conversation_id=conversation_id,
                lease_token=generation_token, ttl_seconds=60,
            )
            try:
                execution = store.begin_tool_execution(
                    owner_id="owner-heartbeat-loss", conversation_id=conversation_id,
                    request_id=request_id, call_id="call-heartbeat-loss",
                    tool_name="send_email", arguments_sha256="b" * 64,
                    lease_token=generation_token,
                )
                store.mark_started_tool_executions_uncertain(
                    owner_id="owner-heartbeat-loss", conversation_id=conversation_id,
                    request_id=request_id, lease_token=generation_token,
                )
            finally:
                store.release_generation_lease(
                    owner_id="owner-heartbeat-loss", conversation_id=conversation_id,
                    lease_token=generation_token,
                )

            lookup_started = threading.Event()
            finish_lookup = threading.Event()
            renewal_failed = threading.Event()

            def fail_renewal(**kwargs):
                renewal_failed.set()
                raise OSError("simulated durable lease-store outage")

            class SlowAdapter:
                provider_id = "mail.example"

                def lookup_tool_outcome(self, identity):
                    lookup_started.set()
                    if not finish_lookup.wait(10):
                        raise TimeoutError("test did not release slow provider lookup")
                    return ProviderOutcomeTests().evidence(
                        execution_id=execution["execution_id"],
                        idempotency_key=execution["idempotency_key"],
                        arguments_sha256=execution["arguments_sha256"],
                    )

            reconciler = ProviderOutcomeReconciler(
                store, ProviderOutcomeRegistry({"send_email": SlowAdapter()}),
                lease_ttl_seconds=5, lease_renew_interval_seconds=1,
            )
            outcomes = []

            def run_reconciliation():
                try:
                    outcomes.append(reconciler.reconcile(
                        owner_id="owner-heartbeat-loss", conversation_id=conversation_id,
                        request_id=request_id, call_id="call-heartbeat-loss",
                    ))
                except Exception as exc:
                    outcomes.append(exc)

            worker = threading.Thread(target=run_reconciliation, daemon=True)
            with patch.object(store, "renew_provider_reconciliation_lease", side_effect=fail_renewal):
                worker.start()
                try:
                    self.assertTrue(lookup_started.wait(5), "provider lookup did not start")
                    self.assertTrue(renewal_failed.wait(5), "heartbeat did not attempt renewal")
                finally:
                    finish_lookup.set()
                    worker.join(timeout=10)

            self.assertFalse(worker.is_alive(), "reconciliation worker did not finish")
            self.assertEqual(len(outcomes), 1)
            self.assertIsInstance(outcomes[0], ConversationConflict)
            self.assertIn("renewal failed", str(outcomes[0]))
            ledger = store.list_provider_outcome_evidence(
                owner_id="owner-heartbeat-loss", conversation_id=conversation_id,
                request_id=request_id, call_id="call-heartbeat-loss",
            )
            self.assertEqual(ledger, [])
            # The worker's finally block releases its own claim even after heartbeat loss.
            store.acquire_provider_reconciliation_lease(
                owner_id="owner-heartbeat-loss", conversation_id=conversation_id,
                request_id=request_id, call_id="call-heartbeat-loss",
                lease_token="verification-worker", ttl_seconds=30,
            )
            self.assertTrue(store.release_provider_reconciliation_lease(
                owner_id="owner-heartbeat-loss", execution_id=execution["execution_id"],
                lease_token="verification-worker",
            ))

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
