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
            def lookup_tool_outcome(self, execution):
                calls.append(execution["execution_id"])
                return evidence
        result = verify_provider_outcome(Adapter(), self.execution)
        self.assertEqual(result.outcome, ProviderOutcome.UNKNOWN)
        self.assertEqual(calls, ["execution-1"])

    def test_adapter_receives_only_minimum_execution_identity(self):
        received = []
        class Adapter:
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

    def test_mismatched_idempotency_key_is_rejected(self):
        class Adapter:
            def lookup_tool_outcome(self, execution):
                return ProviderOutcomeTests().evidence(idempotency_key="d" * 64)
        with self.assertRaisesRegex(ValueError, "fingerprint"):
            verify_provider_outcome(Adapter(), self.execution)

    def test_untyped_adapter_result_is_rejected(self):
        class Adapter:
            def lookup_tool_outcome(self, execution):
                return {"outcome": "confirmed_completed"}
        with self.assertRaisesRegex(TypeError, "untyped"):
            verify_provider_outcome(Adapter(), self.execution)

    def test_lookup_rejected_for_already_completed_execution(self):
        class Adapter:
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



    def test_registry_requires_explicit_adapter_and_reconciler_persists_evidence(self):
        from core.provider_outcome import ProviderOutcomeReconciler, ProviderOutcomeRegistry
        calls = []
        evidence = self.evidence()
        class Adapter:
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

if __name__ == "__main__":
    unittest.main()
