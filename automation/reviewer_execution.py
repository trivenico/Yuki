"""Host-managed execution adapter for bounded independent reviewer tasks.

The reviewer callback receives only the validated task and the exact evidence
references assigned to that task. It never receives session tokens, signing
objects, database handles, or unrestricted repository access. The host claims,
validates, and submits the result around the untrusted reviewer callback.
"""
from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Callable, Mapping
from uuid import uuid4

from automation.reviewer_contracts import ReviewResult, ReviewTask, build_review_result
from automation.reviewer_ledger import ReviewerTaskLedger, ReviewerLedgerError
from automation.reviewer_orchestrator import ProvisionedReviewerTask


class ReviewerExecutionError(RuntimeError):
    """A reviewer task could not be safely executed or recorded."""


@dataclass(frozen=True)
class ReviewerExecutionOutcome:
    task_id: str
    state: str
    result_status: str
    result_digest: str
    blocked_by_host: bool


class ReviewerExecutionAdapter:
    """Execute one provisioned task through a host-controlled boundary."""

    def __init__(
        self,
        ledger: ReviewerTaskLedger,
        *,
        evidence_reader: Callable[[str, str], bytes],
        max_reference_bytes: int = 1_000_000,
        max_total_evidence_bytes: int = 4_000_000,
    ):
        if not isinstance(ledger, ReviewerTaskLedger):
            raise TypeError("reviewer_task_ledger_required")
        if not callable(evidence_reader):
            raise TypeError("trusted_evidence_reader_required")
        if type(max_reference_bytes) is not int or not 1 <= max_reference_bytes <= 10_000_000:
            raise ValueError("invalid_max_reference_bytes")
        if type(max_total_evidence_bytes) is not int or not max_reference_bytes <= max_total_evidence_bytes <= 50_000_000:
            raise ValueError("invalid_max_total_evidence_bytes")
        self.ledger = ledger
        self.evidence_reader = evidence_reader
        self.max_reference_bytes = max_reference_bytes
        self.max_total_evidence_bytes = max_total_evidence_bytes

    def _load_evidence(self, task: ReviewTask) -> Mapping[str, bytes]:
        evidence = {}
        total = 0
        for reference in task.evidence_references:
            try:
                content = self.evidence_reader(reference, task.artifact_sha256)
            except Exception as exc:
                raise ReviewerExecutionError("assigned_evidence_unavailable") from exc
            if not isinstance(content, bytes):
                raise ReviewerExecutionError("evidence_reader_must_return_bytes")
            if len(content) > self.max_reference_bytes:
                raise ReviewerExecutionError("evidence_reference_exceeds_size_limit")
            total += len(content)
            if total > self.max_total_evidence_bytes:
                raise ReviewerExecutionError("total_evidence_exceeds_size_limit")
            evidence[reference] = content
        return MappingProxyType(evidence)

    def execute(
        self,
        *,
        provisioned_task: ProvisionedReviewerTask,
        actor_id: str,
        expected_version: int,
        task: ReviewTask,
        executor: Callable[[ReviewTask, Mapping[str, bytes]], ReviewResult],
        lease_token: str | None = None,
        lease_generation: int | None = None,
    ) -> ReviewerExecutionOutcome:
        if not isinstance(provisioned_task, ProvisionedReviewerTask):
            raise TypeError("provisioned_reviewer_task_required")
        if not isinstance(task, ReviewTask):
            raise TypeError("review_task_required")
        task.validate()
        if (provisioned_task.task_id != task.task_id
                or provisioned_task.reviewer_agent_id != task.reviewer_agent_id
                or provisioned_task.check_name != task.check_name):
            raise ReviewerExecutionError("provisioned_task_binding_mismatch")
        if not callable(executor):
            raise TypeError("reviewer_executor_required")

        effective_lease_token = lease_token if lease_token is not None else provisioned_task.lease_token
        effective_lease_generation = (
            lease_generation if lease_generation is not None
            else provisioned_task.lease_generation
        )
        claimed = self.ledger.claim(
            actor_id=actor_id,
            task_id=task.task_id,
            expected_version=expected_version,
            session_token=provisioned_task.session_token,
            operation_id="claim-" + uuid4().hex,
            lease_token=effective_lease_token,
            lease_generation=effective_lease_generation,
        )
        if claimed.get("state") != "in_progress":
            raise ReviewerExecutionError("review_task_claim_failed")

        blocked = False
        try:
            evidence = self._load_evidence(task)
            result = executor(task, evidence)
            if not isinstance(result, ReviewResult):
                raise ReviewerExecutionError("reviewer_executor_must_return_ReviewResult")
            result.validate(task)
        except Exception as exc:
            # Record an explicit blocked outcome rather than leaving a claimed
            # task indefinitely in progress. This is a host execution failure,
            # not a reviewer assertion or a passed review.
            blocked = True
            result = build_review_result(
                task,
                status="blocked",
                summary="Host-controlled reviewer execution was blocked: "
                        + type(exc).__name__ + ".",
            )

        submitted = self.ledger.submit(
            actor_id=actor_id,
            task_id=task.task_id,
            expected_version=claimed["version"],
            result=result,
            session_token=provisioned_task.session_token,
            operation_id="submit-" + uuid4().hex,
            lease_token=lease_token,
            lease_generation=lease_generation,
        )
        if submitted.get("state") != "submitted":
            raise ReviewerExecutionError("review_result_submission_failed")
        return ReviewerExecutionOutcome(
            task_id=task.task_id,
            state=submitted["state"],
            result_status=result.status,
            result_digest=submitted["result_digest"],
            blocked_by_host=blocked,
        )


__all__ = [
    "ReviewerExecutionAdapter", "ReviewerExecutionError",
    "ReviewerExecutionOutcome",
]
