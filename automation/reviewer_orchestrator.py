"""Coordinator-side reviewer batch planning and session provisioning.

This service prepares durable, independently assigned review tasks and
short-lived worker credentials. It deliberately does not execute reviewer
code or expose signing keys. A trusted host transport is responsible for
delivering each token only to its assigned worker.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable, Mapping
from uuid import uuid4

from automation.reviewer_contracts import ReviewTask
from automation.reviewer_ledger import ReviewerTaskLedger
from automation.reviewer_sessions import ReviewerSessionAuthority
from automation.reviewer_team import ReviewerTeamPlan, build_reviewer_team


class ReviewerOrchestrationError(RuntimeError):
    """A reviewer batch could not be safely prepared."""


@dataclass(frozen=True)
class ProvisionedReviewerTask:
    task_id: str
    check_name: str
    reviewer_agent_id: str
    session_token: str


@dataclass(frozen=True)
class ReviewerBatch:
    artifact_sha256: str
    author_agent_id: str
    plan: ReviewerTeamPlan
    tasks: tuple[ProvisionedReviewerTask, ...]


class ReviewerOrchestrator:
    """Prepare reviewer assignments and scoped credentials under host policy."""

    def __init__(
        self,
        ledger: ReviewerTaskLedger,
        session_authority: ReviewerSessionAuthority,
        *,
        authorize_coordinator: Callable[[str, str, str], bool],
    ):
        if not isinstance(ledger, ReviewerTaskLedger):
            raise TypeError("reviewer_task_ledger_required")
        if not isinstance(session_authority, ReviewerSessionAuthority):
            raise TypeError("reviewer_session_authority_required")
        if not callable(authorize_coordinator):
            raise TypeError("trusted_coordinator_authorizer_required")
        self.ledger = ledger
        self.sessions = session_authority
        self.authorize_coordinator = authorize_coordinator

    def provision_batch(
        self,
        *,
        coordinator_id: str,
        artifact_sha256: str,
        author_agent_id: str,
        agents: Iterable[object],
        requirements_by_check: Mapping[str, Iterable[str]],
        evidence_references_by_check: Mapping[str, Iterable[str]],
        required_checks: Iterable[str] | None = None,
    ) -> ReviewerBatch:
        if not isinstance(coordinator_id, str) or not coordinator_id.strip():
            raise ReviewerOrchestrationError("coordinator_identity_required")
        try:
            if self.authorize_coordinator(
                coordinator_id, "review:orchestrate", artifact_sha256
            ) is not True:
                raise ReviewerOrchestrationError("coordinator_not_authorized")
        except ReviewerOrchestrationError:
            raise
        except Exception as exc:
            raise ReviewerOrchestrationError("coordinator_authorization_failed") from exc

        try:
            plan = build_reviewer_team(
                artifact_sha256=artifact_sha256,
                author_agent_id=author_agent_id,
                agents=agents,
                required_checks=required_checks if required_checks is not None else tuple(requirements_by_check),
            )
        except Exception as exc:
            raise ReviewerOrchestrationError("reviewer_team_planning_failed") from exc

        tasks = []
        tokens = []
        for assignment in plan.assignments:
            requirements = requirements_by_check.get(assignment.check_name)
            references = evidence_references_by_check.get(assignment.check_name, ())
            if isinstance(requirements, (str, bytes)) or requirements is None:
                raise ReviewerOrchestrationError("review_requirements_missing:" + assignment.check_name)
            if isinstance(references, (str, bytes)):
                raise ReviewerOrchestrationError("invalid_evidence_reference_collection:" + assignment.check_name)
            task = ReviewTask(
                task_id="review-" + uuid4().hex,
                artifact_sha256=artifact_sha256,
                author_agent_id=author_agent_id,
                reviewer_agent_id=assignment.reviewer_agent_id,
                check_name=assignment.check_name,
                requirements=tuple(requirements),
                evidence_references=tuple(references),
            )
            try:
                task.validate()
            except Exception as exc:
                raise ReviewerOrchestrationError(
                    "review_task_contract_invalid:" + assignment.check_name
                ) from exc
            tasks.append(task)

        # Mint credentials before persisting assignments. Tokens remain local
        # until create_tasks succeeds; failed batches never return credentials.
        for task in tasks:
            try:
                token = self.sessions.issue(
                    reviewer_agent_id=task.reviewer_agent_id,
                    artifact_sha256=artifact_sha256,
                    actions=("review:execute", "review:submit"),
                )
            except Exception as exc:
                raise ReviewerOrchestrationError(
                    "reviewer_session_provisioning_failed:" + task.reviewer_agent_id
                ) from exc
            tokens.append(token)

        try:
            created = self.ledger.create_tasks(actor_id=coordinator_id, tasks=tuple(tasks))
            if len(created) != len(tasks):
                raise ReviewerOrchestrationError("review_task_persistence_incomplete")
        except ReviewerOrchestrationError:
            raise
        except Exception as exc:
            raise ReviewerOrchestrationError("review_task_persistence_failed") from exc

        return ReviewerBatch(
            artifact_sha256=artifact_sha256,
            author_agent_id=author_agent_id,
            plan=plan,
            tasks=tuple(
                ProvisionedReviewerTask(
                    task_id=task.task_id,
                    check_name=task.check_name,
                    reviewer_agent_id=task.reviewer_agent_id,
                    session_token=token,
                )
                for task, token in zip(tasks, tokens)
            ),
        )


__all__ = [
    "ReviewerOrchestrator", "ReviewerOrchestrationError",
    "ReviewerBatch", "ProvisionedReviewerTask",
]
