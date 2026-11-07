"""Coordinator-side reviewer batch planning and session provisioning.

This service prepares durable, independently assigned review tasks and
short-lived worker credentials. It deliberately does not execute reviewer
code or expose signing keys. A trusted host transport is responsible for
delivering each token only to its assigned worker.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable, Mapping
from pathlib import Path
from uuid import uuid4

from automation.reviewer_contracts import ReviewTask
from automation.reviewer_ledger import ReviewerTaskLedger
from automation.reviewer_sessions import ReviewerSessionAuthority
from automation.reviewer_team import ReviewerTeamPlan, REVIEWER_CAPABILITIES, build_reviewer_team
from automation.reviewer_ledger import ReviewerLedgerConflict


class ReviewerOrchestrationError(RuntimeError):
    """A reviewer batch could not be safely prepared."""


@dataclass(frozen=True)
class ProvisionedReviewerTask:
    task_id: str
    check_name: str
    reviewer_agent_id: str
    session_token: str
    lease_token: str | None = None
    lease_generation: int | None = None
    review_task: ReviewTask | None = None


@dataclass(frozen=True)
class ReviewerRecoveryOutcome:
    task_id: str
    state: str
    previous_reviewer_agent_id: str
    replacement_reviewer_agent_id: str | None
    provisioned_task: ProvisionedReviewerTask | None
    reason: str


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

    def recover_expired_tasks(self, *, coordinator_id: str, agents: Iterable[object], limit: int = 50) -> tuple[ReviewerRecoveryOutcome, ...]:
        """Recover expired in-progress tasks and provision replacement sessions."""
        if self.ledger.lease_authority is None:
            raise ReviewerOrchestrationError("reviewer_lease_authority_required")
        if not isinstance(coordinator_id, str) or not coordinator_id.strip():
            raise ReviewerOrchestrationError("coordinator_identity_required")
        agent_values = tuple(agents)
        try:
            pending = self.ledger.list_expired_in_progress(actor_id=coordinator_id, limit=limit)
        except Exception as exc:
            raise ReviewerOrchestrationError("expired_reviewer_task_discovery_failed") from exc
        outcomes = []
        for item in pending:
            digest, task, previous = item["artifact_sha256"], item["task"], item["reviewer_agent_id"]
            check_name = task["check_name"]
            capability = REVIEWER_CAPABILITIES.get(check_name)
            candidates = []
            for agent in agent_values:
                agent_id = getattr(agent, "agent_id", None)
                capabilities = getattr(agent, "capabilities", ())
                if (isinstance(agent_id, str) and agent_id not in {previous, task.get("author_agent_id")} and
                    getattr(agent, "enabled", False) is True and isinstance(capabilities, (set, frozenset, tuple, list)) and capability in capabilities):
                    candidates.append(agent_id)
            replacement = sorted(set(candidates))[0] if candidates else None
            if replacement is None:
                outcomes.append(ReviewerRecoveryOutcome(item["task_id"], "blocked", previous, None, None, "no_independent_enabled_reviewer"))
                continue
            try:
                if self.authorize_coordinator(coordinator_id, "review:orchestrate", digest) is not True:
                    raise ReviewerOrchestrationError("coordinator_not_authorized")
            except Exception as exc:
                raise ReviewerOrchestrationError("coordinator_authorization_failed") from exc
            session_token = None
            try:
                session_token = self.sessions.issue(reviewer_agent_id=replacement, artifact_sha256=digest, actions=("review:execute", "review:submit"))
                recovered = self.ledger.recover_in_progress(actor_id=coordinator_id, task_id=item["task_id"], expected_version=item["version"], replacement_reviewer_agent_id=replacement, reason="Expired reviewer lease recovered by coordinator")
            except ReviewerLedgerConflict:
                if session_token is not None: self.sessions.revoke_token(session_token)
                outcomes.append(ReviewerRecoveryOutcome(item["task_id"], "superseded", previous, replacement, None, "task_changed_during_recovery"))
                continue
            except Exception as exc:
                if session_token is not None:
                    try: self.sessions.revoke_token(session_token)
                    except Exception: pass
                outcomes.append(ReviewerRecoveryOutcome(item["task_id"], "blocked", previous, replacement, None, "recovery_or_session_provisioning_failed:" + type(exc).__name__))
                continue
            recovered_task = ReviewTask(
                task_id=task["task_id"], artifact_sha256=digest,
                author_agent_id=task["author_agent_id"],
                reviewer_agent_id=replacement, check_name=check_name,
                requirements=tuple(task["requirements"]),
                evidence_references=tuple(task["evidence_references"]),
            )
            recovered_task.validate()
            provisioned = ProvisionedReviewerTask(
                task_id=item["task_id"], check_name=check_name,
                reviewer_agent_id=replacement, session_token=session_token,
                lease_token=recovered["lease_token"],
                lease_generation=recovered["lease_generation"],
                review_task=recovered_task,
            )
            outcomes.append(ReviewerRecoveryOutcome(item["task_id"], "recovered", previous, replacement, provisioned, "expired_lease_recovered"))
        return tuple(outcomes)
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

        if self.ledger.lease_authority is None:
            raise ReviewerOrchestrationError("reviewer_lease_authority_required")
        if Path(self.ledger.lease_authority.database_path).resolve() != Path(self.ledger.database_path).resolve():
            raise ReviewerOrchestrationError("reviewer_lease_database_must_match_ledger")
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
        try:
            for task in tasks:
                token = self.sessions.issue(
                    reviewer_agent_id=task.reviewer_agent_id,
                    artifact_sha256=artifact_sha256,
                    actions=("review:execute", "review:submit"),
                )
                tokens.append(token)
        except Exception as exc:
            for token in tokens:
                try:
                    self.sessions.revoke_token(token)
                except Exception:
                    pass
            raise ReviewerOrchestrationError(
                "reviewer_session_provisioning_failed"
            ) from exc

        try:
            created = self.ledger.create_tasks_with_leases(
                actor_id=coordinator_id, tasks=tuple(tasks)
            )
            if len(created) != len(tasks):
                raise ReviewerOrchestrationError("review_task_persistence_incomplete")
            provisioned = []
            for task, token, receipt in zip(tasks, tokens, created):
                if not isinstance(receipt.get("lease_token"), str):
                    raise ReviewerOrchestrationError("existing_reviewer_task_requires_explicit_resume")
                provisioned.append(ProvisionedReviewerTask(
                    task_id=task.task_id,
                    check_name=task.check_name,
                    reviewer_agent_id=task.reviewer_agent_id,
                    session_token=token,
                    lease_token=receipt["lease_token"],
                    lease_generation=receipt["lease_generation"],
                    review_task=task,
                ))
        except Exception as exc:
            for token in tokens:
                try:
                    self.sessions.revoke_token(token)
                except Exception:
                    pass
            if isinstance(exc, ReviewerOrchestrationError):
                raise
            raise ReviewerOrchestrationError("review_task_and_lease_persistence_failed") from exc

        return ReviewerBatch(
            artifact_sha256=artifact_sha256,
            author_agent_id=author_agent_id,
            plan=plan,
            tasks=tuple(provisioned),
        )


__all__ = [
    "ReviewerOrchestrator", "ReviewerOrchestrationError",
    "ReviewerBatch", "ProvisionedReviewerTask", "ReviewerRecoveryOutcome",
]
