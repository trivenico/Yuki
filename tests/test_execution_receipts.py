import dataclasses
import unittest

from evaluation.evolution_candidates import EvolutionCandidate
from evaluation.execution_receipts import (
    create_execution_manifest,
    create_execution_receipt,
)


class ExecutionReceiptTests(unittest.TestCase):
    def setUp(self):
        self.candidate = EvolutionCandidate(
            candidate_id="candidate-1", owner_id="owner-1", kind="prompt",
            title="Prompt revision", rationale="Improve clarity",
            expected_outcome="More direct answers", evidence_refs=(),
            risk_tags=(), parent_digest=None, payload_digest="a" * 64,
            created_at="2026-11-27T00:00:00+00:00",
            state="accepted_for_evaluation", revision=1,
        )
        self.manifest = create_execution_manifest(
            candidate=self.candidate, artifact=b"exact staged artifact",
            benchmark_id="core", benchmark_version=1,
            benchmark_digest="b" * 64, input_manifest=b'{"inputs":[]}',
            sandbox_image="sha256:" + "c" * 64,
            runtime_identity="oci-runtime/test", runner_version="runner-1",
            created_at="2026-11-27T01:00:00+00:00", attempt_id="attempt-1",
        )

    def test_manifest_hashes_exact_artifact_and_input_bytes(self):
        self.assertEqual(self.manifest.artifact_digest,
                         "e1c1a7b7b6f6c1f36c0d2a7f4a6fbb7a1a2c6b8baf6c4f9f3f6a0e4e9b8d0f2a")
        self.assertTrue(self.manifest.verify())
        changed = dataclasses.replace(self.manifest, artifact_digest="d" * 64)
        self.assertFalse(changed.verify())

    def test_receipt_binds_manifest_and_output_digest(self):
        receipt = create_execution_receipt(
            manifest=self.manifest, output=b"bounded output", exit_code=0,
            timed_out=False, started_at="2026-11-27T01:00:01+00:00",
            completed_at="2026-11-27T01:00:02+00:00",
        )
        self.assertTrue(receipt.verify(self.manifest))
        other_manifest = create_execution_manifest(
            candidate=self.candidate, artifact=b"different artifact",
            benchmark_id="core", benchmark_version=1,
            benchmark_digest="b" * 64, input_manifest=b'{"inputs":[]}',
            sandbox_image="sha256:" + "c" * 64,
            runtime_identity="oci-runtime/test", runner_version="runner-1",
            created_at="2026-11-27T01:00:00+00:00", attempt_id="attempt-1",
        )
        self.assertFalse(receipt.verify(other_manifest))

    def test_rejects_invalid_bounds_and_types(self):
        with self.assertRaises(ValueError):
            create_execution_manifest(
                candidate=self.candidate, artifact="not-bytes",
                benchmark_id="core", benchmark_version=1,
                benchmark_digest="b" * 64, input_manifest=b"",
                sandbox_image="image", runtime_identity="runtime",
                runner_version="runner",
            )
        with self.assertRaises(ValueError):
            create_execution_receipt(
                manifest=self.manifest, output=b"", exit_code=True,
                timed_out=False, started_at="2026-11-27T01:00:01+00:00",
                completed_at="2026-11-27T01:00:02+00:00",
            )
        with self.assertRaises(ValueError):
            create_execution_receipt(
                manifest=self.manifest, output=b"", exit_code=0,
                timed_out=False, started_at="2026-11-27T01:00:02+00:00",
                completed_at="2026-11-27T01:00:01+00:00",
            )

    def test_boolean_integer_confusion_is_rejected(self):
        changed = dataclasses.replace(self.manifest, benchmark_version=True)
        self.assertFalse(changed.verify())


if __name__ == "__main__":
    unittest.main()
