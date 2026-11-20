"""Deterministic structural quality checks for specialist synthesis.

This module evaluates traceability and completeness only. It does not infer
semantic correctness, assign model confidence, or resolve disagreements.
"""
from __future__ import annotations

from dataclasses import dataclass

from automation.specialist_synthesis import PlanSynthesis


@dataclass(frozen=True)
class FindingQuality:
    subtask_id: str
    traceable: bool
    provenance_labeled: bool
    external_verification_claimed: bool
    requires_human_review: bool
    review_reasons: tuple[str, ...]


@dataclass(frozen=True)
class SynthesisQualityReport:
    plan_id: str
    structural_status: str
    expected_findings: int
    received_findings: int
    coverage_complete: bool
    findings: tuple[FindingQuality, ...]
    unresolved_subtasks: tuple[str, ...]
    semantic_correctness_assessed: bool = False
    conflict_resolution_performed: bool = False
    requires_human_review: bool = True


def assess_synthesis_quality(synthesis: PlanSynthesis) -> SynthesisQualityReport:
    """Assess source traceability and structural completeness without changing state."""
    if not isinstance(synthesis, PlanSynthesis):
        raise TypeError("synthesis must be a PlanSynthesis.")
    if synthesis.status not in {"complete", "incomplete"}:
        raise ValueError("unsupported synthesis status.")
    reviewed = []
    seen_subtasks = set()
    seen_tasks = set()
    for finding in synthesis.findings:
        reasons = []
        traceable = (
            isinstance(finding.subtask_id, str) and bool(finding.subtask_id.strip())
            and isinstance(finding.task_id, str) and bool(finding.task_id.strip())
            and isinstance(finding.specialist, str) and bool(finding.specialist.strip())
        )
        if not traceable:
            reasons.append("missing_source_identity")
        if finding.subtask_id in seen_subtasks:
            reasons.append("duplicate_subtask_finding")
        if finding.task_id in seen_tasks:
            reasons.append("duplicate_task_reference")
        seen_subtasks.add(finding.subtask_id)
        seen_tasks.add(finding.task_id)
        provenance_labeled = finding.evidence_basis == "supplied_material_only"
        if not provenance_labeled:
            reasons.append("unsupported_provenance_label")
        if not finding.verified_externally:
            reasons.append("external_verification_not_performed")
        reviewed.append(FindingQuality(
            subtask_id=finding.subtask_id,
            traceable=traceable,
            provenance_labeled=provenance_labeled,
            external_verification_claimed=finding.verified_externally,
            requires_human_review=True,
            review_reasons=tuple(reasons),
        ))
    unresolved = tuple(sorted(set(synthesis.incomplete_subtasks) | set(synthesis.failed_subtasks)))
    structural_ok = (
        synthesis.status == "complete"
        and not unresolved
        and all(item.traceable and item.provenance_labeled for item in reviewed)
        and len(seen_subtasks) == len(reviewed)
        and len(seen_tasks) == len(reviewed)
    )
    return SynthesisQualityReport(
        plan_id=synthesis.plan_id,
        structural_status="structurally_complete" if structural_ok else "needs_review",
        expected_findings=len(reviewed) + len(unresolved),
        received_findings=len(reviewed),
        coverage_complete=structural_ok,
        findings=tuple(reviewed),
        unresolved_subtasks=unresolved,
    )
