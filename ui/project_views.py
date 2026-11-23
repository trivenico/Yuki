"""Owner-scoped project views for the Textual supervision dashboard."""
from __future__ import annotations

from core.project_intelligence import ProjectTaskEvidence, build_project_progress


def render_projects(registry, *, actor_id: str, limit: int = 50) -> str:
    projects = registry.list_projects(owner_id=actor_id, limit=limit)
    if not projects:
        return "No projects yet. Create one with /project create NAME."
    return "\n".join(
        f"{item.name} | {item.project_id} | created={item.created_at}"
        for item in projects
    )


def render_project(registry, task_runtime, *, actor_id: str, project_id: str) -> str:
    task_ids = registry.list_task_ids(owner_id=actor_id, project_id=project_id, limit=500)
    evidence = []
    unavailable = []
    for task_id in task_ids:
        try:
            task = task_runtime.get(actor_id=actor_id, task_id=task_id)
        except Exception:
            unavailable.append(task_id)
            continue
        evidence.append(ProjectTaskEvidence(
            task_id=task.task_id, owner_id=actor_id, state=task.state,
            updated_at=task.updated_at, dependencies=tuple(task.dependencies),
        ))
    report = build_project_progress(
        project_id=project_id, owner_id=actor_id, tasks=evidence,
    )
    lines = [report.render()]
    if unavailable:
        lines.append("Tasks not readable in this session: " + ", ".join(unavailable))
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
