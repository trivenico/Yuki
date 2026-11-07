"""Bridge submitted reviewer-ledger results into verification evidence.

Only submitted results for the exact artifact and expected author/check set are
eligible. The evidence store still performs its own trusted authorization and
execution-receipt validation; this bridge does not execute or authenticate work.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable, Iterable

from automation.independent_verification import VerificationCheck, VerificationReport
from automation.verification_evidence_store import VerificationEvidenceStore
from automation.reviewer_ledger import ReviewerTaskLedger


class ReviewerVerificationBridgeError(RuntimeError):
    """Reviewer results cannot safely form a verification report."""


class ReviewerVerificationBridge:
    def __init__(self, ledger: ReviewerTaskLedger, evidence_store: VerificationEvidenceStore, *,
                 evidence_reference_validator: Callable[[str, str], bool]):
        if not isinstance(ledger, ReviewerTaskLedger):
            raise TypeError("reviewer_task_ledger_required")
        if not isinstance(evidence_store, VerificationEvidenceStore):
            raise TypeError("verification_evidence_store_required")
        if not callable(evidence_reference_validator):
            raise TypeError("trusted_evidence_reference_validator_required")
        self.ledger = ledger
        self.evidence_store = evidence_store
        self.evidence_reference_validator = evidence_reference_validator

    def submit_report(
        self,
        *,
        actor_id: str,
        artifact_sha256: str,
        author_agent_id: str,
        task_ids: Iterable[str],
        required_checks: Iterable[str] | None = None,
    ) -> dict:
        if not isinstance(artifact_sha256, str) or len(artifact_sha256) != 64:
            raise ReviewerVerificationBridgeError("invalid_artifact_digest")
        if not isinstance(author_agent_id, str) or not author_agent_id.strip():
            raise ReviewerVerificationBridgeError("author_identity_required")
        ids = tuple(task_ids)
        if not ids or len(ids) > 100 or len(set(ids)) != len(ids):
            raise ReviewerVerificationBridgeError("unique_submitted_task_ids_required")
        required = tuple(required_checks) if required_checks is not None else None
        if required is not None and (not required or len(set(required)) != len(required)):
            raise ReviewerVerificationBridgeError("invalid_required_checks")

        checks = []
        seen_checks = set()
        for task_id in ids:
            record = self.ledger.get(actor_id=actor_id, task_id=task_id)
            if record["state"] != "submitted" or not isinstance(record.get("result"), dict):
                raise ReviewerVerificationBridgeError("review_task_not_submitted")
            if record.get("signature_verified") is not True or not isinstance(record.get("signature"), dict):
                raise ReviewerVerificationBridgeError("review_result_signature_not_verified")
            task = record["task"]
            result = record["result"]
            if task["artifact_sha256"] != artifact_sha256:
                raise ReviewerVerificationBridgeError("review_artifact_digest_mismatch")
            if task["author_agent_id"] != author_agent_id:
                raise ReviewerVerificationBridgeError("review_author_identity_mismatch")
            if result.get("task_id") != task_id or result.get("artifact_sha256") != artifact_sha256:
                raise ReviewerVerificationBridgeError("review_result_binding_mismatch")
            if result.get("reviewer_agent_id") != task.get("reviewer_agent_id"):
                raise ReviewerVerificationBridgeError("reviewer_identity_mismatch")
            for reference in task.get("evidence_references", ()):
                if self.evidence_reference_validator(reference, artifact_sha256) is not True:
                    raise ReviewerVerificationBridgeError("review_task_evidence_reference_invalid")
            for finding in result.get("findings", ()):
                reference = finding.get("evidence_reference")
                if self.evidence_reference_validator(reference, artifact_sha256) is not True:
                    raise ReviewerVerificationBridgeError("review_finding_evidence_reference_invalid")
            check_name = result.get("check_name")
            if check_name != task.get("check_name") or check_name in seen_checks:
                raise ReviewerVerificationBridgeError("duplicate_or_mismatched_review_check")
            if result.get("status") not in {"passed", "failed", "blocked", "not_run"}:
                raise ReviewerVerificationBridgeError("invalid_submitted_review_status")
            digest = record.get("result_digest")
            if not isinstance(digest, str) or len(digest) != 64:
                raise ReviewerVerificationBridgeError("submitted_result_digest_missing")
            seen_checks.add(check_name)
            checks.append(VerificationCheck(
                name=check_name,
                reviewer_agent_id=result["reviewer_agent_id"],
                status=result["status"],
                evidence_reference=f"yuki-review-result://{task_id}/{digest}",
                summary=result["summary"],
            ))

        if required is not None and set(seen_checks) != set(required):
            raise ReviewerVerificationBridgeError("submitted_reviews_must_match_required_checks")
        report = VerificationReport(
            artifact_sha256=artifact_sha256,
            author_agent_id=author_agent_id,
            checks=tuple(checks),
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        return self.evidence_store.record(report, required_checks=required)


__all__ = ["ReviewerVerificationBridge", "ReviewerVerificationBridgeError"]
