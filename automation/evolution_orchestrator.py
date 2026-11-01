"""Human-gated orchestration for the Yuki evolution lifecycle.

This coordinates existing planner, sandbox, reviewer, verifier and ledger
adapters. Adapters remain responsible for authenticating their own evidence
and enforcing their own security boundaries. No merge or deployment occurs.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any, Callable

from automation.evolution_review_verifier import (
    EvolutionGateDecision,
    EvolutionReviewVerifier,
    ReviewEvidence,
    VerificationReport,
)

_SHA = re.compile(r"^[a-f0-9]{40,64}$")
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}$")


class EvolutionOrchestrationError(RuntimeError):
    """A lifecycle stage failed; subsequent stages are not run."""


@dataclass(frozen=True)
class EvolutionPlan:
    plan_id: str
    actor_id: str
    summary: str
    changes: tuple[dict[str, Any], ...]
    plan_digest: str


@dataclass(frozen=True)
class EvolutionExecution:
    commit_sha: str
    execution_id: str
    result_ref: str


@dataclass(frozen=True)
class EvolutionOrchestrationResult:
    plan_id: str
    plan_digest: str
    execution: EvolutionExecution
    decision: EvolutionGateDecision
    status: str


def plan_digest(*, plan_id: str, actor_id: str, summary: str,
                changes: tuple[dict[str, Any], ...]) -> str:
    """Canonical digest for a plan's identity, intent and proposed changes."""
    payload = {
        "plan_id": plan_id,
        "actor_id": actor_id,
        "summary": summary,
        "changes": changes,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=False, allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class EvolutionOrchestrator:
    """Sequence plan -> explicit approval -> supervised execution -> review -> verify."""

    def __init__(
        self,
        *,
        planner: Callable[[str, str], EvolutionPlan],
        approval_verifier: Callable[[EvolutionPlan], bool],
        executor: Callable[[EvolutionPlan], EvolutionExecution],
        reviewer: Callable[[EvolutionPlan, EvolutionExecution], ReviewEvidence],
        verification_provider: Callable[[EvolutionPlan, EvolutionExecution], VerificationReport],
        ledger: Callable[[dict[str, Any]], None],
        review_verifier: EvolutionReviewVerifier | None = None,
    ):
        callbacks = (planner, approval_verifier, executor, reviewer,
                     verification_provider, ledger)
        if any(not callable(item) for item in callbacks):
            raise TypeError("all lifecycle adapters, including the ledger, are required.")
        self.planner = planner
        self.approval_verifier = approval_verifier
        self.executor = executor
        self.reviewer = reviewer
        self.verification_provider = verification_provider
        self.ledger = ledger
        self.review_verifier = review_verifier or EvolutionReviewVerifier()

    @staticmethod
    def _validate_plan(plan: EvolutionPlan, actor_id: str) -> EvolutionPlan:
        if not isinstance(plan, EvolutionPlan):
            raise EvolutionOrchestrationError("planner returned an invalid plan.")
        if not isinstance(plan.plan_id, str) or not _ID.fullmatch(plan.plan_id):
            raise EvolutionOrchestrationError("plan identity is invalid.")
        if plan.actor_id != actor_id:
            raise EvolutionOrchestrationError("plan actor does not match authenticated requester.")
        if not isinstance(plan.summary, str) or not plan.summary.strip() or len(plan.summary) > 4000:
            raise EvolutionOrchestrationError("plan summary is invalid.")
        if not isinstance(plan.changes, tuple) or not 1 <= len(plan.changes) <= 8:
            raise EvolutionOrchestrationError("plan must contain one to eight proposed changes.")
        for change in plan.changes:
            if not isinstance(change, dict):
                raise EvolutionOrchestrationError("plan change entries must be objects.")
            try:
                encoded = json.dumps(change, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False, allow_nan=False).encode("utf-8")
            except (TypeError, ValueError) as exc:
                raise EvolutionOrchestrationError("plan contains non-canonical change data.") from exc
            if len(encoded) > 256 * 1024:
                raise EvolutionOrchestrationError("plan change entry exceeds size limit.")
        expected = plan_digest(plan_id=plan.plan_id, actor_id=plan.actor_id,
                               summary=plan.summary, changes=plan.changes)
        if not isinstance(plan.plan_digest, str) or not re.fullmatch(r"[a-f0-9]{64}", plan.plan_digest):
            raise EvolutionOrchestrationError("plan digest is invalid.")
        if plan.plan_digest != expected:
            raise EvolutionOrchestrationError("plan digest does not match canonical plan content.")
        return plan

    @staticmethod
    def _assert_plan_integrity(plan: EvolutionPlan) -> None:
        current = plan_digest(plan_id=plan.plan_id, actor_id=plan.actor_id,
                              summary=plan.summary, changes=plan.changes)
        if current != plan.plan_digest:
            raise EvolutionOrchestrationError("plan content changed after approval; execution blocked.")

    def _record(self, actor_id: str, plan: EvolutionPlan | None, stage: str,
                metadata: dict[str, Any] | None = None) -> None:
        event = {
            "actor_id": actor_id,
            "plan_id": plan.plan_id if plan else None,
            "plan_digest": plan.plan_digest if plan else None,
            "stage": stage,
            "metadata": metadata or {},
        }
        try:
            self.ledger(event)
        except Exception as exc:
            raise EvolutionOrchestrationError("durable evolution ledger unavailable; stopping.") from exc

    def run(self, *, actor_id: str, task: str) -> EvolutionOrchestrationResult:
        if not isinstance(actor_id, str) or not _ID.fullmatch(actor_id):
            raise ValueError("authenticated actor_id is required.")
        if not isinstance(task, str) or not task.strip() or len(task) > 2000:
            raise ValueError("task must be bounded non-empty text.")
        self._record(actor_id, None, "started")
        try:
            plan = self._validate_plan(self.planner(actor_id, task), actor_id)
        except EvolutionOrchestrationError:
            raise
        except Exception as exc:
            raise EvolutionOrchestrationError("planning failed; no execution was started.") from exc
        self._record(actor_id, plan, "planned")

        try:
            approved = self.approval_verifier(plan)
        except Exception as exc:
            self._record(actor_id, plan, "approval_verification_failed")
            raise EvolutionOrchestrationError("approval verification failed closed.") from exc
        if approved is not True:
            self._record(actor_id, plan, "approval_denied")
            raise EvolutionOrchestrationError("explicit approval for this exact plan is required.")
        self._record(actor_id, plan, "approved")
        self._assert_plan_integrity(plan)

        try:
            execution = self.executor(plan)
        except Exception as exc:
            self._record(actor_id, plan, "execution_failed")
            raise EvolutionOrchestrationError("supervised execution failed.") from exc
        if not isinstance(execution, EvolutionExecution):
            self._record(actor_id, plan, "execution_invalid")
            raise EvolutionOrchestrationError("executor returned invalid execution evidence.")
        if (not isinstance(execution.commit_sha, str) or not _SHA.fullmatch(execution.commit_sha)
                or not isinstance(execution.execution_id, str) or not _ID.fullmatch(execution.execution_id)
                or not isinstance(execution.result_ref, str) or not execution.result_ref
                or len(execution.result_ref) > 512):
            self._record(actor_id, plan, "execution_invalid")
            raise EvolutionOrchestrationError("execution evidence is malformed.")
        self._assert_plan_integrity(plan)
        self._record(actor_id, plan, "executed", {"execution_id": execution.execution_id,
                                                  "commit_sha": execution.commit_sha})

        try:
            review = self.reviewer(plan, execution)
            report = self.verification_provider(plan, execution)
            decision = self.review_verifier.decide(review, report,
                                                   expected_commit=execution.commit_sha)
        except Exception as exc:
            self._record(actor_id, plan, "review_or_verification_failed",
                         {"execution_id": execution.execution_id})
            raise EvolutionOrchestrationError("review or verification evidence invalid; merge blocked.") from exc
        status = "ready_for_human_merge_review" if decision.allowed_to_propose_merge else "blocked"
        self._record(actor_id, plan, "verified" if decision.allowed_to_propose_merge else "blocked",
                     {"execution_id": execution.execution_id, "reasons": list(decision.reasons)})
        return EvolutionOrchestrationResult(plan.plan_id, plan.plan_digest,
                                            execution, decision, status)
