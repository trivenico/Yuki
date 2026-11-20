"""Explicit, human-authored disagreement records for specialist findings.

No text similarity or model inference is used to detect conflicts. A caller must
supply the shared claim key and each reviewer's position explicitly.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ReviewPosition:
    finding_task_id: str
    specialist: str
    position: str
    rationale: str


@dataclass(frozen=True)
class SpecialistDisagreement:
    plan_id: str
    claim_key: str
    positions: tuple[ReviewPosition, ...]
    status: str = "unresolved"
    requires_human_review: bool = True


def record_disagreement(
    *,
    plan_id: str,
    claim_key: str,
    positions: tuple[ReviewPosition, ...],
) -> SpecialistDisagreement:
    """Create a bounded disagreement record; never select a winning position."""
    for name, value, limit in (
        ("plan_id", plan_id, 100),
        ("claim_key", claim_key, 160),
    ):
        if not isinstance(value, str) or not value.strip() or len(value) > limit or "\x00" in value:
            raise ValueError(f"{name} must be non-empty bounded text.")
    if not isinstance(positions, tuple) or not 2 <= len(positions) <= 10:
        raise ValueError("positions must contain 2-10 explicit review positions.")
    task_ids = set()
    specialists = set()
    normalized_positions = set()
    for item in positions:
        if not isinstance(item, ReviewPosition):
            raise TypeError("positions must contain ReviewPosition values.")
        for name, value, limit in (
            ("finding_task_id", item.finding_task_id, 100),
            ("specialist", item.specialist, 80),
            ("position", item.position, 500),
            ("rationale", item.rationale, 1200),
        ):
            if not isinstance(value, str) or not value.strip() or len(value) > limit or "\x00" in value:
                raise ValueError(f"{name} must be non-empty bounded text.")
        if item.finding_task_id in task_ids:
            raise ValueError("each position must reference a distinct task result.")
        task_ids.add(item.finding_task_id)
        specialists.add(item.specialist)
        normalized_positions.add(item.position.strip().casefold())
    if len(normalized_positions) < 2:
        raise ValueError("positions must explicitly disagree.")
    if len(specialists) < 2:
        raise ValueError("disagreement must include at least two distinct specialists.")
    return SpecialistDisagreement(
        plan_id=plan_id.strip(),
        claim_key=claim_key.strip(),
        positions=positions,
    )
