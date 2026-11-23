"""Owner-scoped project views for the Textual supervision dashboard."""
from __future__ import annotations

from core.project_intelligence import ProjectTaskEvidence, build_project_progress
from core.task_evidence import TaskEvidenceStore


def render_projects(registry, *, actor_id: str, limit: int = 50) -> str:
    projects = registry.list_projects(owner_id=actor_id, limit=limit)
    if not projects:
        return "No projects yet. Create one with /project create NAME."
    return "\n".join(
        f"{item.name} | {item.project_id} | created={item.created_at}"
        for item in projects
    )


def render_project(registry, task_runtime, *, actor_id: str, project_id: str, task_evidence_store: TaskEvidenceStore | None = None) -> str:
    task_ids = registry.list_task_ids(owner_id=actor_id, project_id=project_id, limit=500)
    evidence = []
    provenance = []
    unavailable = []
    for task_id in task_ids:
        try:
            task = task_runtime.get(actor_id=actor_id, task_id=task_id)
        except Exception:
            unavailable.append(task_id)
            continue
        verified = ()
        if task_evidence_store is not None and type(getattr(task, "version", None)) is int:
            verified = task_evidence_store.list_verified(
                owner_id=actor_id, task_id=task.task_id,
                task_version=task.version, limit=10,
            )
        human_review = next((item for item in verified if item.kind == "human_review"), None)
        workflow_verified = any(item.kind == "ci_workflow" for item in verified)
        artifacts_verified = workflow_verified and any(item.kind == "test_report" for item in verified) and any(
            item.kind == "artifact" for item in verified
        )
        evidence.append(ProjectTaskEvidence(
            task_id=task.task_id, owner_id=actor_id, state=task.state,
            updated_at=task.updated_at, dependencies=tuple(task.dependencies),
            verification_ref=human_review.reference if human_review else None,
            ci_workflow_verified=workflow_verified, artifacts_verified=artifacts_verified,
        ))
        provenance.extend((task.task_id, item) for item in verified)
    report = build_project_progress(
        project_id=project_id, owner_id=actor_id, tasks=evidence,
    )
    lines = [report.render()]
    if unavailable:
        lines.append("Tasks not readable in this session: " + ", ".join(unavailable))
    if task_evidence_store is not None:
        lines.append("Verified evidence (current task attempts):")
        if not provenance:
            lines.append("  none")
        for task_id, item in provenance:
            lines.append(
                f"  {task_id} | {item.kind} | {item.reference} | sha256={item.digest_sha256}"
            )
    milestones = registry.list_milestones(owner_id=actor_id, project_id=project_id)
    lines.append("Milestones:")
    if not milestones:
        lines.append("  none")
    for milestone in milestones:
        due = f" due={milestone.due_at}" if milestone.due_at else ""
        proof = " verified" if milestone.verification_ref else ""
        lines.append(
            f"  {milestone.title} | {milestone.milestone_id} | {milestone.status} "
            f"v{milestone.version}{due}{proof}"
        )
    lines.append(
        "Commands: /project add PROJECT_ID TASK_ID · /project remove PROJECT_ID TASK_ID · "
        "/project milestone PROJECT_ID TITLE · /project complete MILESTONE_ID VERSION PROOF"
    )
    return "\n".join(lines)
