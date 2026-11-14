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
            def record_provider_outcome_evidence(self, **kwargs):
                return {"outcome": kwargs["evidence"].outcome.value}
        registry = ProviderOutcomeRegistry({"send_email": Adapter()})
        reconciler = ProviderOutcomeReconciler(Store(), registry)
        result = reconciler.reconcile(
            owner_id="owner-1", conversation_id="conversation-1",
            request_id="request-1", call_id="call-1",
        )
        self.assertEqual(result["outcome"], "unknown")
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["execution_id"], "execution-1")

    def test_registry_does_not_guess_missing_provider_adapter(self):
        from core.provider_outcome import ProviderOutcomeRegistry
        registry = ProviderOutcomeRegistry({})
        with self.assertRaisesRegex(LookupError, "no provider outcome adapter"):
            registry.lookup("send_email", self.execution)

if __name__ == "__main__":
    unittest.main()
