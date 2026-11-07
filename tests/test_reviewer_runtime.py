from unittest import TestCase
from unittest.mock import patch

from automation.reviewer_ledger import ReviewerTaskLedger
from automation.reviewer_runtime import build_isolated_reviewer_adapter


class ReviewerRuntimeCompositionTests(TestCase):
    def test_composes_host_adapter_and_isolated_executor(self):
        ledger = object.__new__(ReviewerTaskLedger)
        evidence_reader = lambda reference, digest: b"evidence"
        with patch("automation.reviewer_runtime.DockerReviewerExecutor") as executor_type, patch(
            "automation.reviewer_runtime.ReviewerExecutionAdapter"
        ) as adapter_type:
            adapter = object()
            executor = object()
            adapter_type.return_value = adapter
            executor_type.return_value = executor

            result = build_isolated_reviewer_adapter(
                ledger,
                evidence_reader=evidence_reader,
                image="registry.example/reviewer@sha256:" + "a" * 64,
                command_argv=("python", "-m", "reviewer_worker"),
            )

        self.assertEqual(result, (adapter, executor))
        adapter_type.assert_called_once_with(ledger, evidence_reader=evidence_reader)
        executor_type.assert_called_once()

    def test_rejects_untrusted_ledger_value(self):
        with self.assertRaisesRegex(TypeError, "trusted_reviewer_ledger_required"):
            build_isolated_reviewer_adapter(
                object(),
                evidence_reader=lambda _reference, _digest: b"",
                image="registry.example/reviewer@sha256:" + "a" * 64,
                command_argv=("python", "-m", "reviewer_worker"),
            )

    def test_rejects_missing_evidence_reader(self):
        ledger = object.__new__(ReviewerTaskLedger)
        with self.assertRaisesRegex(TypeError, "trusted_evidence_reader_required"):
            build_isolated_reviewer_adapter(
                ledger,
                evidence_reader=None,
                image="registry.example/reviewer@sha256:" + "a" * 64,
                command_argv=("python", "-m", "reviewer_worker"),
            )
