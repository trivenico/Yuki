"""Read-only, provenance-preserving synthesis of specialist plan results."""
from __future__ import annotations

from dataclasses import dataclass

from automation.agent_coordinator import SpecialistAgentCoordinator
from automation.specialist_result import SpecialistResult, validate_specialist_result


@dataclass(frozen=True)
class SpecialistFinding:
    subtask_id: str
    task_id: str
    specialist: str
    output: str
    evidence_basis: str
    verified_externally: bool


@dataclass(frozen=True)
class PlanSynthesis:
    plan_id: str
    status: str
    findings: tuple[SpecialistFinding, ...]
    incomplete_subtasks: tuple[str, ...]
    failed_subtasks: tuple[str, ...]
    conflict_review: str = "not_performed"
    requires_human_review: bool = True


def synthesize_plan_results(
    *,
    coordinator: SpecialistAgentCoordinator,
    actor_id: str,
    plan_id: str,
) -> PlanSynthesis:
    """Collect authorized completed results without changing tasks or adjudicating claims.

    The caller needs agent:read and task:read authorization. Result payloads are
    revalidated against the text-only contract; malformed legacy/custom results
    are rejected rather than silently promoted to evidence.
    """
    if not isinstance(coordinator, SpecialistAgentCoordinator):
        raise TypeError("coordinator must be a SpecialistAgentCoordinator.")
    plan = coordinator.get_plan(actor_id=actor_id, plan_id=plan_id)
    findings = []
    incomplete = []
    failed = []
    for subtask in plan.subtasks:
        if subtask.status in {"failed", "cancelled", "blocked"}:
            failed.append(subtask.subtask_id)
            continue
        if subtask.task_id is None:
            incomplete.append(subtask.subtask_id)
            continue
        task = coordinator.runtime.get(actor_id=actor_id, task_id=subtask.task_id)
        if task.state != "succeeded":
            if task.state in {"failed", "cancelled", "interrupted"}:
                failed.append(subtask.subtask_id)
            else:
                incomplete.append(subtask.subtask_id)
            continue
        payload = task.result
        if not isinstance(payload, dict) or not isinstance(payload.get("provenance"), dict):
            raise ValueError(f"subtask {subtask.subtask_id} has no supported result envelope.")
        provenance = payload["provenance"]
        result = SpecialistResult(
            specialist=payload.get("specialist"),
            output=payload.get("output"),
            schema_version=payload.get("schema_version"),
            output_kind=payload.get("output_kind"),
            evidence_basis=provenance.get("evidence_basis"),
            verified_externally=provenance.get("verified_externally"),
            limitations=tuple(payload.get("limitations", ())),
        )
        validate_specialist_result(result)
        if result.specialist != subtask.agent_id:
            raise ValueError(f"subtask {subtask.subtask_id} result specialist does not match its assignment.")
        findings.append(SpecialistFinding(
            subtask_id=subtask.subtask_id,
            task_id=subtask.task_id,
            specialist=result.specialist,
            output=result.output,
            evidence_basis=result.evidence_basis,
            verified_externally=result.verified_externally,
        ))
    status = "complete" if not incomplete and not failed else "incomplete"
    return PlanSynthesis(
        plan_id=plan.plan_id,
        status=status,
        findings=tuple(findings),
        incomplete_subtasks=tuple(incomplete),
        failed_subtasks=tuple(failed),
    )
