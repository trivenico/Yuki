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
        class Adapter:
            def lookup_tool_outcome(self, execution):
                calls.append(execution["execution_id"])
                return self.evidence()
            evidence = self.evidence
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


if __name__ == "__main__":
    unittest.main()
