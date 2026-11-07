"""Supervised worker loop for durable reviewer dispatches.

The worker keeps bearer credentials in a host-provided resolver; the outbox
contains identifiers and task payloads only. A dispatch is acknowledged only
after the ledger reports a signed submitted result. If execution may have
claimed the task but then fails, the dispatch is deliberately left claimed for
lease expiry and supervised recovery rather than blindly replaying work.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Mapping

from automation.reviewer_contracts import ReviewTask, ReviewResult
from automation.reviewer_execution import (
    ReviewerExecutionAdapter,
    ReviewerExecutionOutcome,
)
from automation.reviewer_ledger import ReviewerTaskLedger
from automation.reviewer_orchestrator import ProvisionedReviewerTask


class ReviewerWorkerError(RuntimeError):
    """A reviewer worker could not safely process a dispatch."""


@dataclass(frozen=True)
class ReviewerWorkerReport:
    worker_id: str
    claimed: int
    completed: int
    retried: int
    deferred: int
    outcomes: tuple[ReviewerExecutionOutcome, ...]


class ReviewerWorker:
    """Run a bounded number of assigned reviewer jobs as one host worker.

    credential_resolver must read credentials from trusted host storage and
    return the current provisioned contract for the claimed task. It must
    never source credentials from the dispatch payload.
    """

    def __init__(
        self,
        ledger: ReviewerTaskLedger,
        execution_adapter: ReviewerExecutionAdapter,
        *,
        worker_id: str,
        credential_resolver: Callable[[Mapping[str, object]], ProvisionedReviewerTask],
        executor: Callable[[ReviewTask, Mapping[str, bytes]], ReviewResult],
        max_attempts: int = 5,
        claim_seconds: int = 300,
    ) -> None:
        if not isinstance(ledger, ReviewerTaskLedger):
            raise TypeError("reviewer_task_ledger_required")
        if not isinstance(execution_adapter, ReviewerExecutionAdapter):
            raise TypeError("reviewer_execution_adapter_required")
        ledger_worker_id = ledger._actor(worker_id)
        if not callable(credential_resolver):
            raise TypeError("trusted_reviewer_credential_resolver_required")
        if not callable(executor):
            raise TypeError("reviewer_executor_required")
        if type(max_attempts) is not int or not 1 <= max_attempts <= 20:
            raise ValueError("invalid_reviewer_worker_max_attempts")
        if type(claim_seconds) is not int or not 10 <= claim_seconds <= 900:
            raise ValueError("invalid_reviewer_worker_claim_ttl")
        self.ledger = ledger
        self.execution_adapter = execution_adapter
        self.worker_id = ledger_worker_id
        self.credential_resolver = credential_resolver
        self.executor = executor
        self.max_attempts = max_attempts
        self.claim_seconds = claim_seconds

    def run_once(self, *, limit: int = 1) -> ReviewerWorkerReport:
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("invalid_reviewer_worker_batch_limit")
        claims = self.ledger.claim_dispatches(
            actor_id=self.worker_id,
            worker_id=self.worker_id,
            limit=limit,
            claim_seconds=self.claim_seconds,
            max_attempts=self.max_attempts,
        )
        completed = retried = deferred = 0
        outcomes: list[ReviewerExecutionOutcome] = []

        for claim in claims:
            dispatch_id = claim.get("dispatch_id")
            claim_token = claim.get("claim_token")
            task_id = claim.get("task_id")
            try:
                if not isinstance(dispatch_id, str) or not isinstance(claim_token, str):
                    raise ReviewerWorkerError("dispatch_claim_payload_invalid")
                task_data = claim.get("task")
                if not isinstance(task_data, dict):
                    raise ReviewerWorkerError("dispatch_task_payload_invalid")
                task_data = dict(task_data)
                task_data["reviewer_agent_id"] = claim.get("reviewer_agent_id")
                task = ReviewTask(
                    task_id=task_data["task_id"],
                    artifact_sha256=task_data["artifact_sha256"],
                    author_agent_id=task_data["author_agent_id"],
                    reviewer_agent_id=task_data["reviewer_agent_id"],
                    check_name=task_data["check_name"],
                    requirements=tuple(task_data["requirements"]),
                    evidence_references=tuple(task_data["evidence_references"]),
                )
                task.validate()
                if task.task_id != task_id or task.artifact_sha256 != claim.get("artifact_sha256"):
                    raise ReviewerWorkerError("dispatch_task_binding_mismatch")
                provisioned = self.credential_resolver(claim)
                if not isinstance(provisioned, ProvisionedReviewerTask):
                    raise ReviewerWorkerError("credential_resolver_contract_invalid")
                if (
                    provisioned.task_id != task.task_id
                    or provisioned.reviewer_agent_id != self.worker_id
                    or provisioned.check_name != task.check_name
                    or provisioned.review_task != task
                    or not provisioned.session_token
                    or not provisioned.lease_token
                    or type(provisioned.lease_generation) is not int
                ):
                    raise ReviewerWorkerError("host_credentials_do_not_match_dispatch")
            except Exception as exc:
                # Failures before ledger claim are safe to retry: no review
                # execution has started and no task state has been consumed.
                try:
                    self.ledger.fail_dispatch(
                        actor_id=self.worker_id,
                        dispatch_id=dispatch_id,
                        claim_token=claim_token,
                        error_code="worker.provisioning." + type(exc).__name__,
                        max_attempts=self.max_attempts,
                    )
                    retried += 1
                except Exception:
                    deferred += 1
                continue

            try:
                record = self.ledger.get(actor_id=self.worker_id, task_id=task.task_id)
                if record["state"] == "submitted":
                    if record.get("signature_verified") is not True:
                        raise ReviewerWorkerError("submitted_result_not_verified")
                    self.ledger.complete_dispatch(
                        actor_id=self.worker_id,
                        dispatch_id=dispatch_id,
                        claim_token=claim_token,
                    )
                    completed += 1
                    continue
                if record["state"] != "assigned":
                    # An in-progress task may belong to a worker that crashed.
                    # Do not replay it here; lease supervision owns recovery.
                    deferred += 1
                    continue
                outcome = self.execution_adapter.execute(
                    provisioned_task=provisioned,
                    actor_id=self.worker_id,
                    expected_version=record["version"],
                    task=task,
                    executor=self.executor,
                )
                outcomes.append(outcome)
                verified = self.ledger.get(actor_id=self.worker_id, task_id=task.task_id)
                if verified["state"] != "submitted" or verified.get("signature_verified") is not True:
                    raise ReviewerWorkerError("review_submission_not_durably_verified")
                self.ledger.complete_dispatch(
                    actor_id=self.worker_id,
                    dispatch_id=dispatch_id,
                    claim_token=claim_token,
                )
                completed += 1
            except Exception:
                # Once execution may have claimed the ledger task, blind retry
                # can duplicate work. Leave the dispatch claim to expire; the
                # task lease supervisor decides whether it is recoverable.
                deferred += 1

        return ReviewerWorkerReport(
            worker_id=self.worker_id,
            claimed=len(claims),
            completed=completed,
            retried=retried,
            deferred=deferred,
            outcomes=tuple(outcomes),
        )


__all__ = ["ReviewerWorker", "ReviewerWorkerError", "ReviewerWorkerReport"]
