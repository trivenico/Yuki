"""Read-time verifier for persisted execution-backed evaluation evidence.

The run record binds the receipt digests; this helper reopens the separate
receipt journal and verifies the exact cross-store association.
"""
from __future__ import annotations

from dataclasses import dataclass

from evaluation.execution_receipt_store import CandidateExecutionReceiptStore
from evaluation.run_store import EvaluationRun, EvaluationRunStore


@dataclass(frozen=True, slots=True)
class VerifiedExecutionEvidenceBinding:
    """Exact, integrity-checked join across a run and its receipt journal."""

    run_id: str
    run_record_digest: str
    owner_id: str
    candidate_id: str
    candidate_digest: str
    benchmark_id: str
    benchmark_version: int
    benchmark_digest: str
    attempt_id: str
    manifest_digest: str
    artifact_digest: str
    receipt_digest: str


def verify_execution_backed_run(
    *,
    run: EvaluationRun,
    run_store: EvaluationRunStore,
    receipt_store: CandidateExecutionReceiptStore,
    attempt_id: str,
) -> VerifiedExecutionEvidenceBinding:
    """Verify that a completed evaluation run exactly matches a stored receipt.

    A successful return means the two stores currently contain mutually
    consistent integrity-checked records. It is not sandbox attestation,
    authenticated owner identity, atomic cross-database commit, or proof that
    the runtime actually loaded the recorded artifact.
    """
    if not isinstance(run, EvaluationRun):
        raise TypeError("run must be an EvaluationRun")
    if not callable(getattr(run_store, "verify", None)):
        raise TypeError("run_store must provide verify")
    if not isinstance(receipt_store, CandidateExecutionReceiptStore):
        raise TypeError("receipt_store must be a CandidateExecutionReceiptStore")
    if not isinstance(attempt_id, str) or not attempt_id.strip() or len(attempt_id) > 128:
        raise ValueError("attempt_id must be bounded non-empty text")
    if not run_store.verify(run):
        raise ValueError("evaluation run failed integrity verification")
    if not run.candidate_digest:
        raise ValueError("evaluation run is not bound to a candidate digest")
    if not (run.execution_attempt_id and run.execution_manifest_digest
            and run.execution_artifact_digest and run.execution_receipt_digest):
        raise ValueError("evaluation run has no persisted execution evidence binding")
    if run.execution_attempt_id != attempt_id.strip():
        raise ValueError("requested attempt does not match persisted run binding")

    manifest, receipt = receipt_store.get(
        owner_id=run.owner_id, attempt_id=attempt_id.strip()
    )
    expected = (
        manifest.owner_id == run.owner_id
        and manifest.candidate_id == run.candidate_id
        and manifest.candidate_digest == run.candidate_digest
        and manifest.benchmark_id == run.benchmark_id
        and manifest.benchmark_version == run.benchmark_version
        and manifest.benchmark_digest == run.benchmark_digest
        and receipt.attempt_id == manifest.attempt_id
        and receipt.manifest_digest == manifest.manifest_digest
        and receipt.artifact_digest == manifest.artifact_digest
        and run.execution_manifest_digest == manifest.manifest_digest
        and run.execution_artifact_digest == manifest.artifact_digest
        and run.execution_receipt_digest == receipt.receipt_digest
    )
    if not expected:
        raise ValueError("execution receipt does not match evaluation run identity")
    if receipt.exit_code != 0 or receipt.timed_out:
        raise ValueError("execution receipt does not represent a successful completed attempt")

    return VerifiedExecutionEvidenceBinding(
        run_id=run.run_id,
        run_record_digest=run.record_digest,
        owner_id=run.owner_id,
        candidate_id=run.candidate_id,
        candidate_digest=run.candidate_digest,
        benchmark_id=run.benchmark_id,
        benchmark_version=run.benchmark_version,
        benchmark_digest=run.benchmark_digest,
        attempt_id=manifest.attempt_id,
        manifest_digest=manifest.manifest_digest,
        artifact_digest=manifest.artifact_digest,
        receipt_digest=receipt.receipt_digest,
    )
