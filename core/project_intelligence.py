"""Project-level progress intelligence over explicitly supplied task membership.

The host must authorize and assemble project membership before calling this module.
This module does not infer project membership, mutate tasks, or execute recommendations.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from itertools import islice
from typing import Iterable


_STATES = frozenset({
    "queued", "waiting_approval", "running", "succeeded", "failed",
    "cancelled", "interrupted", "blocked",
})
_MAX_TASKS = 500
_MAX_ID = 200


@dataclass(frozen=True, slots=True)
class ProjectTaskEvidence:
    """Minimal task metadata plus an explicit host-verified project association."""

    task_id: str
    owner_id: str
    state: str
    updated_at: str
    dependencies: tuple[str, ...] = ()
    verification_ref: str | None = None
    human_review_verified: bool = False
    ci_workflow_verified: bool = False
    artifacts_verified: bool = False


@dataclass(frozen=True, slots=True)
class ProjectRecommendation:
    code: str
    message: str
    evidence_task_ids: tuple[str, ...]
    confidence: str = "observed"


@dataclass(frozen=True, slots=True)
class ProjectProgressReport:
    project_id: str
    owner_id: str
    generated_at: str
    total_tasks: int
    counts: dict[str, int]
    verified_completed: int
    succeeded_unverified: int
    ci_workflow_verified: int
    artifacts_verified: int
    measured_progress_percent: float
    stale_task_ids: tuple[str, ...]
    unresolved_dependency_task_ids: tuple[str, ...]
    recommendations: tuple[ProjectRecommendation, ...]

    def render(self) -> str:
        lines = [
            f"Project progress: {self.project_id}",
            f"Tasks: {self.total_tasks}; human-reviewed complete: {self.verified_completed}; "
            f"succeeded but not human-reviewed: {self.succeeded_unverified}",
            f"CI evidence: workflows verified={self.ci_workflow_verified}; "
            f"test artifacts verified={self.artifacts_verified}",
            f"Measured progress: {self.measured_progress_percent:.1f}% "
            "(human-reviewed tasks / explicitly associated tasks)",
            "States: " + ", ".join(
                f"{state}={self.counts.get(state, 0)}"
                for state in ("queued", "waiting_approval", "running", "succeeded",
                              "failed", "cancelled", "interrupted", "blocked", "other")
            ),
        ]
        if self.stale_task_ids:
            lines.append("Potentially stale (age heuristic): " + ", ".join(self.stale_task_ids))
        if self.unresolved_dependency_task_ids:
            lines.append("Tasks with unresolved dependencies: " +
                         ", ".join(self.unresolved_dependency_task_ids))
        for item in self.recommendations:
            evidence = ", ".join(item.evidence_task_ids) or "none"
            lines.append(f"Recommendation [{item.code}; {item.confidence}]: "
                         f"{item.message} (evidence: {evidence})")
        lines.append("Progress is measured from explicit verification references; "
                     "age-based signals are estimates, not proof of a stalled milestone.")
        return "\n".join(lines)



@dataclass(frozen=True, slots=True)
class ProjectNextActionReport:
    """Advisory task shortlist; never dispatches work or grants authorization."""

    project_id: str
    owner_id: str
    ready_task_ids: tuple[str, ...]
    blocked_task_ids: tuple[str, ...]
    waiting_task_ids: tuple[str, ...]
    reasons: tuple[str, ...]

    def render(self) -> str:
        lines = [
            f"Next-task review for project {self.project_id}:",
            "Ready for an approval check: " + (", ".join(self.ready_task_ids) or "none"),
            "Blocked by dependencies: " + (", ".join(self.blocked_task_ids) or "none"),
            "Waiting or already active: " + (", ".join(self.waiting_task_ids) or "none"),
        ]
        lines.extend("Reason: " + reason for reason in self.reasons)
        lines.append("This is advisory only; it does not dispatch tasks or grant permissions.")
        return "\n".join(lines)


def recommend_next_project_tasks(
    *,
    project_id: str,
    owner_id: str,
    tasks: Iterable[ProjectTaskEvidence],
    limit: int = 5,
) -> ProjectNextActionReport:
    """Return a deterministic, owner-scoped shortlist using current verified task evidence."""
    if type(limit) is not int or not 1 <= limit <= 20:
        raise ValueError("limit must be an integer from 1 to 20.")
    # Reuse the canonical bounded validator and owner-isolation checks.
    snapshot = tuple(islice(iter(tasks), _MAX_TASKS + 1))
    build_project_progress(project_id=project_id, owner_id=owner_id, tasks=snapshot)
    by_id = {item.task_id: item for item in snapshot}
    ready: list[str] = []
    blocked: list[str] = []
    waiting: list[str] = []
    reasons: list[str] = []
    for item in snapshot:
        if item.state != "queued":
            if item.state in {"running", "waiting_approval"}:
                waiting.append(item.task_id)
            continue
        if not item.dependencies:
            ready.append(item.task_id)
            continue
        dependencies = [by_id.get(dep) for dep in item.dependencies]
        if any(dep is None for dep in dependencies):
            blocked.append(item.task_id)
            reasons.append(f"{item.task_id}: one or more dependencies are outside the authorized project snapshot.")
        elif any(dep.state != "succeeded" for dep in dependencies):
            blocked.append(item.task_id)
            reasons.append(f"{item.task_id}: one or more dependencies have not succeeded.")
        elif any(not dep.human_review_verified for dep in dependencies):
            blocked.append(item.task_id)
            reasons.append(f"{item.task_id}: succeeded dependencies still require human review.")
        else:
            ready.append(item.task_id)
    return ProjectNextActionReport(
        project_id=project_id, owner_id=owner_id,
        ready_task_ids=tuple(sorted(ready)[:limit]),
        blocked_task_ids=tuple(sorted(blocked)[:limit]),
        waiting_task_ids=tuple(sorted(waiting)[:limit]),
        reasons=tuple(reasons[:limit * 2]),
    )

def build_project_progress(
    *,
    project_id: str,
    owner_id: str,
    tasks: Iterable[ProjectTaskEvidence],
    now: datetime | None = None,
    stale_after_days: int = 7,
) -> ProjectProgressReport:
    """Aggregate a bounded, owner-consistent, explicit project task snapshot."""
    for name, value in (("project_id", project_id), ("owner_id", owner_id)):
        if not isinstance(value, str) or not value.strip() or len(value) > _MAX_ID or "\x00" in value:
            raise ValueError(f"{name} must be non-empty bounded text.")
    project_id = project_id.strip()
    owner_id = owner_id.strip()
    if type(stale_after_days) is not int or not 1 <= stale_after_days <= 90:
        raise ValueError("stale_after_days must be an integer from 1 to 90.")
    instant = now or datetime.now(timezone.utc)
    if not isinstance(instant, datetime) or instant.tzinfo is None or instant.utcoffset() is None:
        raise ValueError("now must be timezone-aware.")
    instant = instant.astimezone(timezone.utc)
    try:
        items = tuple(islice(iter(tasks), _MAX_TASKS + 1))
    except TypeError as exc:
        raise ValueError("tasks must be an iterable of ProjectTaskEvidence values.") from exc
    if len(items) > _MAX_TASKS:
        raise ValueError("project task snapshot exceeds the 500-task limit.")
    if any(not isinstance(item, ProjectTaskEvidence) for item in items):
        raise ValueError("every task must be ProjectTaskEvidence.")
    for item in items:
        if not isinstance(item.task_id, str) or not item.task_id.strip() or len(item.task_id) > _MAX_ID or "\x00" in item.task_id:
            raise ValueError("task IDs must be non-empty bounded text.")
    if len({item.task_id for item in items}) != len(items):
        raise ValueError("project task IDs must be unique.")
    ids = {item.task_id for item in items}
    counts: Counter[str] = Counter()
    verified = 0
    succeeded_unverified = 0
    ci_workflow_verified = 0
    artifacts_verified = 0
    stale: list[str] = []
    unresolved: list[str] = []
    queued: list[str] = []
    blocked: list[str] = []
    for item in items:
        if not isinstance(item.owner_id, str) or item.owner_id.strip() != owner_id:
            raise PermissionError("project snapshot contains a task from another owner.")
        if not isinstance(item.state, str) or item.state not in _STATES:
            raise ValueError("task state is not recognized.")
        if not isinstance(item.dependencies, tuple) or len(item.dependencies) > _MAX_TASKS:
            raise ValueError("dependencies must be a bounded tuple.")
        if any(not isinstance(dep, str) or not dep or len(dep) > _MAX_ID or "\x00" in dep for dep in item.dependencies):
            raise ValueError("dependency IDs must be bounded text.")
        if len(set(item.dependencies)) != len(item.dependencies) or item.task_id in item.dependencies:
            raise ValueError("dependencies must be unique and cannot be self-references.")
        if item.verification_ref is not None and (
            not isinstance(item.verification_ref, str) or not item.verification_ref.strip()
            or len(item.verification_ref) > 500 or "\x00" in item.verification_ref
        ):
            raise ValueError("verification_ref must be bounded non-empty text when supplied.")
        if (
            type(item.human_review_verified) is not bool
            or type(item.ci_workflow_verified) is not bool
            or type(item.artifacts_verified) is not bool
        ):
            raise ValueError("evidence verification indicators must be booleans.")
        if item.human_review_verified and not item.verification_ref:
            raise ValueError("human review verification requires an explicit reference.")
        if item.artifacts_verified and not item.ci_workflow_verified:
            raise ValueError("artifact verification requires a verified workflow.")
        counts[item.state] += 1
        ci_workflow_verified += int(item.ci_workflow_verified)
        artifacts_verified += int(item.artifacts_verified)
        if item.state == "succeeded":
            if item.human_review_verified:
                verified += 1
            else:
                succeeded_unverified += 1
        if item.state == "queued":
            queued.append(item.task_id)
        if item.state == "blocked":
            blocked.append(item.task_id)
        if any(dep not in ids for dep in item.dependencies) or any(
            next((candidate.state for candidate in items if candidate.task_id == dep), None)
            not in {"succeeded"} for dep in item.dependencies
        ):
            if item.dependencies:
                unresolved.append(item.task_id)
        try:
            updated = datetime.fromisoformat(item.updated_at)
            if updated.tzinfo is None or updated.utcoffset() is None:
                raise ValueError
            if instant - updated.astimezone(timezone.utc) >= timedelta(days=stale_after_days):
                stale.append(item.task_id)
        except (TypeError, ValueError):
            # Invalid or timezone-naive timestamps are not evidence of staleness.
            pass
    recommendations: list[ProjectRecommendation] = []
    if blocked:
        recommendations.append(ProjectRecommendation(
            "review_blocked_tasks", "Review the blocked tasks and their recorded reasons.",
            tuple(sorted(blocked)),
        ))
    if unresolved:
        recommendations.append(ProjectRecommendation(
            "resolve_dependencies", "Inspect explicit dependencies that are missing or not succeeded.",
            tuple(sorted(unresolved)),
        ))
    if stale:
        recommendations.append(ProjectRecommendation(
            "review_stale_activity", "Check whether these tasks still reflect current work; age alone does not prove a stall.",
            tuple(sorted(stale)), "heuristic",
        ))
    if queued:
        recommendations.append(ProjectRecommendation(
            "review_queued_work", "Review queued tasks and confirm their prerequisites and authorization before dispatch.",
            tuple(sorted(queued)),
        ))
    total = len(items)
    return ProjectProgressReport(
        project_id=project_id, owner_id=owner_id,
        generated_at=instant.isoformat(timespec="seconds"), total_tasks=total,
        counts={key: counts.get(key, 0) for key in sorted(_STATES)},
        verified_completed=verified, succeeded_unverified=succeeded_unverified,
        ci_workflow_verified=ci_workflow_verified, artifacts_verified=artifacts_verified,
        measured_progress_percent=(verified / total * 100.0) if total else 0.0,
        stale_task_ids=tuple(sorted(stale)),
        unresolved_dependency_task_ids=tuple(sorted(unresolved)),
        recommendations=tuple(recommendations),
    )


@dataclass(frozen=True, slots=True)
class ProjectResumeAssessment:
    """Evidence-aware resumption snapshot; recommendations are non-executing."""

    project_id: str
    owner_id: str
    generated_at: str
    progress: ProjectProgressReport
    next_actions: ProjectNextActionReport
    current_goal: str | None
    recent_risks: tuple[str, ...]
    previous_decisions: tuple[str, ...]
    open_milestone_titles: tuple[str, ...]
    completed_milestone_titles: tuple[str, ...]
    resume_recommendations: tuple[str, ...]

    def render(self) -> str:
        lines = [
            f"Project resume assessment: {self.project_id}",
            f"Goal: {self.current_goal or 'No goal recorded'}",
            f"Milestones: {len(self.completed_milestone_titles)} completed; {len(self.open_milestone_titles)} open",
            f"Verified progress: {self.progress.verified_completed}/{self.progress.total_tasks} tasks "
            f"({self.progress.measured_progress_percent:.1f}%)",
            "Next ready tasks: " + (", ".join(self.next_actions.ready_task_ids) or "none"),
            "Blocked tasks: " + (", ".join(self.next_actions.blocked_task_ids) or "none"),
            "Recent risks: " + (" | ".join(self.recent_risks) or "none recorded"),
            "Previous decisions: " + (" | ".join(self.previous_decisions) or "none recorded"),
        ]
        lines.extend("Resume recommendation: " + item for item in self.resume_recommendations)
        lines.append("Assessment is advisory. Live task state and current authorization take precedence over historical notes.")
        return "\n".join(lines)


def build_project_resume_assessment(
    *,
    project_id: str,
    owner_id: str,
    tasks: Iterable[ProjectTaskEvidence],
    awareness_events: Iterable[object] = (),
    milestones: Iterable[object] = (),
    now: datetime | None = None,
    limit: int = 5,
) -> ProjectResumeAssessment:
    """Combine current verified task state with bounded project history and milestones."""
    instant = now or datetime.now(timezone.utc)
    progress = build_project_progress(
        project_id=project_id, owner_id=owner_id, tasks=tasks, now=instant,
    )
    task_snapshot = tuple(islice(iter(tasks), _MAX_TASKS + 1))
    next_actions = recommend_next_project_tasks(
        project_id=project_id, owner_id=owner_id, tasks=task_snapshot, limit=limit,
    )
    events = tuple(islice(iter(awareness_events), 100))
    milestone_items = tuple(islice(iter(milestones), 100))
    if any(getattr(event, "owner_id", None) != owner_id or
           getattr(event, "project_id", None) != project_id for event in events):
        raise PermissionError("awareness history contains an event outside this project owner scope.")
    if any(getattr(item, "owner_id", None) != owner_id or
           getattr(item, "project_id", None) != project_id for item in milestone_items):
        raise PermissionError("milestones contain a record outside this project owner scope.")
    allowed_kinds = {"goal_set", "milestone_recorded", "task_outcome_verified", "risk_recorded", "planning_decision"}
    if any(getattr(event, "event_kind", None) not in allowed_kinds or
           not isinstance(getattr(event, "summary", None), str) or len(event.summary) > 1200
           for event in events):
        raise ValueError("awareness history contains an invalid event.")
    if any(getattr(item, "status", None) not in {"open", "completed"} or
           not isinstance(getattr(item, "title", None), str) or len(item.title) > 200
           for item in milestone_items):
        raise ValueError("milestone snapshot contains an invalid record.")
    chronological = sorted(events, key=lambda event: (event.created_at, event.event_id))
    goals = [event for event in chronological if event.event_kind == "goal_set"]
    risks = [event.summary for event in reversed(chronological) if event.event_kind == "risk_recorded"]
    decisions = [event.summary for event in reversed(chronological) if event.event_kind == "planning_decision"]
    ready = next_actions.ready_task_ids
    recommendations: list[str] = []
    if goals:
        recommendations.append("Resume against the latest recorded goal, then confirm it is still current.")
    else:
        recommendations.append("Record or confirm the active project goal before selecting new work.")
    if ready:
        recommendations.append("Review ready tasks and obtain the required approval before dispatch.")
    if next_actions.blocked_task_ids:
        recommendations.append("Resolve blocked dependencies and review their evidence before advancing.")
    if risks:
        recommendations.append("Review recent recorded risks before approving further work.")
    if progress.succeeded_unverified:
        recommendations.append("Obtain human review for succeeded tasks that lack verified completion evidence.")
    open_milestones = tuple(item.title for item in milestone_items if item.status == "open")
    completed_milestones = tuple(item.title for item in milestone_items if item.status == "completed")
    if open_milestones:
        recommendations.append("Check open milestone scope and due dates against current task progress.")
    return ProjectResumeAssessment(
        project_id=project_id, owner_id=owner_id, generated_at=instant.astimezone(timezone.utc).isoformat(timespec="seconds"),
        progress=progress, next_actions=next_actions,
        current_goal=goals[-1].summary if goals else None,
        recent_risks=tuple(risks[:10]), previous_decisions=tuple(decisions[:10]),
        open_milestone_titles=open_milestones, completed_milestone_titles=completed_milestones,
        resume_recommendations=tuple(recommendations[:limit + 4]),
    )
