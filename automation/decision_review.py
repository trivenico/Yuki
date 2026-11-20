"""Read-only engineering decision review packet assembly.

This module organizes existing specialist evidence for a human decision-maker.
It does not generate a winning decision, approve changes, or execute work.
"""
from __future__ import annotations

from dataclasses import dataclass

from automation.specialist_quality import SynthesisQualityReport, assess_synthesis_quality
from automation.specialist_review import SpecialistDisagreement
from automation.specialist_synthesis import PlanSynthesis


@dataclass(frozen=True)
class EngineeringDecisionReview:
    plan_id: str
    quality: SynthesisQualityReport
    disagreements: tuple[SpecialistDisagreement, ...]
    review_status: str
    proposed_decision: str | None
    requires_human_review: bool = True
    changes_authorized: bool = False


def build_decision_review(
    *,
    synthesis: PlanSynthesis,
    disagreements: tuple[SpecialistDisagreement, ...] = (),
) -> EngineeringDecisionReview:
    """Build a traceable review packet and keep every decision with a human."""
    quality = assess_synthesis_quality(synthesis)
    if not isinstance(disagreements, tuple):
        raise TypeError("disagreements must be an immutable tuple.")
    available_task_ids = {item.task_id for item in synthesis.findings}
    for disagreement in disagreements:
        if not isinstance(disagreement, SpecialistDisagreement):
            raise TypeError("disagreements must contain SpecialistDisagreement values.")
        if disagreement.plan_id != synthesis.plan_id:
            raise ValueError("disagreement belongs to a different plan.")
        if disagreement.status != "unresolved" or disagreement.requires_human_review is not True:
            raise ValueError("only unresolved human-review disagreements are accepted.")
        referenced = {position.finding_task_id for position in disagreement.positions}
        if not referenced.issubset(available_task_ids):
            raise ValueError("disagreement references a task outside this synthesis.")
    status = "blocked_for_review" if not quality.coverage_complete or disagreements else "ready_for_human_decision"
    return EngineeringDecisionReview(
        plan_id=synthesis.plan_id,
        quality=quality,
        disagreements=disagreements,
        review_status=status,
        proposed_decision=None,
    )
