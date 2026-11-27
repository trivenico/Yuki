import tempfile
import unittest
from pathlib import Path

from evaluation.evolution_candidates import EvolutionCandidate
from evaluation.execution_evidence_binding import verify_execution_backed_run
from evaluation.execution_receipts import create_execution_manifest, create_execution_receipt
from evaluation.execution_receipt_store import CandidateExecutionReceiptStore
from evaluation.model_eval import EvalCase, ModelEvaluator
from evaluation.run_store import EvaluationRunStore


class ExecutionEvidenceBindingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.run_store = EvaluationRunStore(root / "runs.sqlite3")
        self.receipt_store = CandidateExecutionReceiptStore(root / "receipts.sqlite3")
        self.candidate = EvolutionCandidate(
            candidate_id="candidate-a", owner_id="owner-a", kind="prompt",
            title="Title", rationale="Reason", expected_outcome="Outcome",
            evidence_refs=(), risk_tags=(), parent_digest=None,
            payload_digest="a" * 64, created_at="2026-11-27T00:00:00+00:00",
            state="accepted_for_evaluation", revision=1,
        )
        self.manifest = create_execution_manifest(
            candidate=self.candidate, artifact=b"exact artifact",
            benchmark_id="benchmark-a", benchmark_version=2,
            benchmark_digest="b" * 64, input_manifest=b'{"inputs":[]}',
            sandbox_image="sha256:" + "c" * 64, runtime_identity="runtime/1",
            runner_version="runner/1", attempt_id="attempt-a",
            created_at="2026-11-27T01:00:00+00:00",
        )
        self.receipt = create_execution_receipt(
            manifest=self.manifest, output=b"ok", exit_code=0, timed_out=False,
            started_at="2026-11-27T01:00:01+00:00",
            completed_at="2026-11-27T01:00:02+00:00",
        )
        self.receipt_store.record(
            owner_id="owner-a", manifest=self.manifest, receipt=self.receipt
        )
        report = ModelEvaluator().run(
            "candidate-a", lambda prompt: "benchmark completed",
            [EvalCase("case", "reasoning", "Run the benchmark", required_terms=("benchmark",))],
        )
        self.run = self.run_store.record(
            owner_id="owner-a", idempotency_key="run-a",
            benchmark_id="benchmark-a", benchmark_version=2,
            benchmark_digest="b" * 64, candidate_id="candidate-a",
            provider_id="local", model_id="test-model",
            environment={"platform": "linux"}, report=report,
            candidate_digest="a" * 64,
            execution_attempt_id="attempt-a", receipt_store=self.receipt_store,
        )

    def test_exact_run_and_receipt_join(self):
        binding = verify_execution_backed_run(
            run=self.run, run_store=self.run_store,
            receipt_store=self.receipt_store, attempt_id="attempt-a",
        )
        self.assertEqual(binding.run_id, self.run.run_id)
        self.assertEqual(binding.manifest_digest, self.manifest.manifest_digest)
        self.assertEqual(binding.receipt_digest, self.receipt.receipt_digest)
        self.assertEqual(binding.artifact_digest, self.manifest.artifact_digest)

    def test_missing_receipt_fails(self):
        with self.assertRaises(KeyError):
            verify_execution_backed_run(
                run=self.run, run_store=self.run_store,
                receipt_store=self.receipt_store, attempt_id="missing",
            )

    def test_benchmark_mismatch_fails(self):
        # Create a valid receipt for a different benchmark digest.
        wrong = create_execution_manifest(
            candidate=self.candidate, artifact=b"exact artifact",
            benchmark_id="benchmark-a", benchmark_version=2,
            benchmark_digest="d" * 64, input_manifest=b'{"inputs":[]}',
            sandbox_image="sha256:" + "c" * 64, runtime_identity="runtime/1",
            runner_version="runner/1", attempt_id="attempt-b",
            created_at="2026-11-27T01:00:00+00:00",
        )
        wrong_receipt = create_execution_receipt(
            manifest=wrong, output=b"ok", exit_code=0, timed_out=False,
            started_at="2026-11-27T01:00:01+00:00",
            completed_at="2026-11-27T01:00:02+00:00",
        )
        self.receipt_store.record(
            owner_id="owner-a", manifest=wrong, receipt=wrong_receipt
        )
        with self.assertRaisesRegex(ValueError, "does not match"):
            verify_execution_backed_run(
                run=self.run, run_store=self.run_store,
                receipt_store=self.receipt_store, attempt_id="attempt-b",
            )

    def test_failed_or_timed_out_execution_is_not_accepted(self):
        other = create_execution_manifest(
            candidate=self.candidate, artifact=b"another artifact",
            benchmark_id="benchmark-a", benchmark_version=2,
            benchmark_digest="b" * 64, input_manifest=b'{"inputs":[]}',
            sandbox_image="sha256:" + "c" * 64, runtime_identity="runtime/1",
            runner_version="runner/1", attempt_id="attempt-failed",
            created_at="2026-11-27T01:00:00+00:00",
        )
        failed = create_execution_receipt(
            manifest=other, output=b"failed", exit_code=1, timed_out=False,
            started_at="2026-11-27T01:00:01+00:00",
            completed_at="2026-11-27T01:00:02+00:00",
        )
        self.receipt_store.record(owner_id="owner-a", manifest=other, receipt=failed)
        with self.assertRaisesRegex(ValueError, "successful"):
            verify_execution_backed_run(
                run=self.run, run_store=self.run_store,
                receipt_store=self.receipt_store, attempt_id="attempt-failed",
            )


if __name__ == "__main__":
    unittest.main()
