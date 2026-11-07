"""Materialize reviewer-team assignments as artifact-bound review tasks.

This layer is deliberately side-effect free: it creates contracts only. It
never enqueues work, executes a reviewer, or writes a verification attestation.
"""
from __future__ import annotations

from hashlib import sha256
from typing import Mapping, Sequence

from automation.reviewer_contracts import ReviewTask
from automation.reviewer_team import ReviewerTeamPlan, REVIEWER_CAPABILITIES


class ReviewerWorkflowError(ValueError):
    """Reviewer task materialization failed closed."""


def build_review_tasks(
    plan: ReviewerTeamPlan,
    *,
    requirements_by_check: Mapping[str, Sequence[str]],
    evidence_references_by_check: Mapping[str, Sequence[str]],
) -> tuple[ReviewTask, ...]:
    """Convert a complete reviewer plan into deterministic, bounded task contracts.

    Requirements and evidence references must be supplied by the caller for
    every assigned check. The builder does not invent review criteria or claim
    that referenced evidence has been authenticated.
    """
    if not isinstance(plan, ReviewerTeamPlan):
        raise TypeError("reviewer_team_plan_required")
    if not isinstance(requirements_by_check, Mapping) or not isinstance(evidence_references_by_check, Mapping):
        raise ReviewerWorkflowError("review_inputs_must_be_mappings")
    assignments = plan.assignments
    if not isinstance(assignments, tuple) or not assignments:
        raise ReviewerWorkflowError("review_assignments_required")
    check_names = tuple(item.check_name for item in assignments)
    if len(set(check_names)) != len(check_names) or any(name not in REVIEWER_CAPABILITIES for name in check_names):
        raise ReviewerWorkflowError("review_assignments_invalid")
    if set(requirements_by_check) != set(check_names):
        raise ReviewerWorkflowError("requirements_must_match_assigned_checks")
    if set(evidence_references_by_check) != set(check_names):
        raise ReviewerWorkflowError("evidence_references_must_match_assigned_checks")

    tasks = []
    used_reviewers = set()
    for assignment in assignments:
        reviewer_id = assignment.reviewer_agent_id
        if not isinstance(reviewer_id, str) or not reviewer_id.strip() or reviewer_id in used_reviewers:
            raise ReviewerWorkflowError("reviewer_identity_missing_or_reused")
        if reviewer_id == plan.author_agent_id:
            raise ReviewerWorkflowError("author_cannot_be_assigned_as_reviewer")
        used_reviewers.add(reviewer_id)
        requirements = requirements_by_check[assignment.check_name]
        evidence = evidence_references_by_check[assignment.check_name]
        if not isinstance(requirements, (tuple, list)) or not isinstance(evidence, (tuple, list)):
            raise ReviewerWorkflowError("review_inputs_must_be_sequences")
        material = "\0".join((
            "yuki-review-task-v1", plan.artifact_sha256,
            assignment.check_name, reviewer_id,
        ))
        task_id = "review-" + sha256(material.encode("utf-8")).hexdigest()[:32]
        task = ReviewTask(
            task_id=task_id,
            artifact_sha256=plan.artifact_sha256,
            author_agent_id=plan.author_agent_id,
            reviewer_agent_id=reviewer_id,
            check_name=assignment.check_name,
            requirements=tuple(requirements),
            evidence_references=tuple(evidence),
        )
        try:
            task.validate()
        except (TypeError, ValueError) as exc:
            raise ReviewerWorkflowError("invalid_review_task:" + assignment.check_name) from exc
        tasks.append(task)
    return tuple(tasks)


__all__ = ["ReviewerWorkflowError", "build_review_tasks"]
