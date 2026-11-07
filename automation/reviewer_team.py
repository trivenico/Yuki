"""Deterministic reviewer-team assignment and separation-of-duties policy.

This module creates review assignments only. It does not execute reviewers or
claim that an assignment is an attestation. Submission still passes through
VerificationEvidenceStore's authenticated authorizer.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

REVIEWER_CAPABILITIES = {
    "acceptance_criteria": "review_requirements",
    "tests": "review_testing",
    "security": "review_security",
    "architecture": "review_architecture",
    "claims_and_provenance": "review_provenance",
}


class ReviewerTeamError(ValueError):
    """Reviewer assignments are incomplete or violate separation of duties."""


@dataclass(frozen=True)
class ReviewerAssignment:
    check_name: str
    reviewer_agent_id: str


@dataclass(frozen=True)
class ReviewerTeamPlan:
    artifact_sha256: str
    author_agent_id: str
    assignments: tuple[ReviewerAssignment, ...]


def build_reviewer_team(
    *,
    artifact_sha256: str,
    author_agent_id: str,
    agents: Iterable[object],
    required_checks: Iterable[str] = tuple(REVIEWER_CAPABILITIES),
) -> ReviewerTeamPlan:
    """Select distinct enabled specialists with explicit review capabilities.

    Selection is deterministic. Each check receives a different reviewer, and
    no reviewer may be the artifact author. This creates a plan, not proof that
    a review occurred.
    """
    import re

    if not isinstance(artifact_sha256, str) or not re.fullmatch(r"[a-f0-9]{64}", artifact_sha256):
        raise ReviewerTeamError("invalid_artifact_sha256")
    if not isinstance(author_agent_id, str) or not author_agent_id.strip():
        raise ReviewerTeamError("author_agent_id_required")
    checks = tuple(required_checks)
    if not checks or len(set(checks)) != len(checks) or any(c not in REVIEWER_CAPABILITIES for c in checks):
        raise ReviewerTeamError("unsupported_or_duplicate_review_checks")

    candidates = []
    for agent in agents:
        agent_id = getattr(agent, "agent_id", None)
        capabilities = getattr(agent, "capabilities", ())
        enabled = getattr(agent, "enabled", False)
        if (
            isinstance(agent_id, str) and agent_id != author_agent_id
            and enabled is True and isinstance(capabilities, (set, frozenset, tuple, list))
        ):
            candidates.append((agent_id, frozenset(capabilities)))
    candidates.sort(key=lambda item: item[0])

    assignments = []
    used = set()
    for check in checks:
        capability = REVIEWER_CAPABILITIES[check]
        reviewer = next(
            (agent_id for agent_id, capabilities in candidates
             if agent_id not in used and capability in capabilities),
            None,
        )
        if reviewer is None:
            raise ReviewerTeamError("independent_reviewer_unavailable:" + check)
        used.add(reviewer)
        assignments.append(ReviewerAssignment(check, reviewer))
    return ReviewerTeamPlan(artifact_sha256, author_agent_id, tuple(assignments))
