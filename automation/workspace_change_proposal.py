"""Prepare immutable, reviewable workspace-change proposals without applying them.

This module bridges a completed human decision review to the existing
ApprovedWorkspaceChanges service. It does not write files, execute tests, or
treat proposal creation as authorization.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json

from automation.approved_workspace_changes import FileChange
from automation.decision_review import EngineeringDecisionReview

_MAX_CHANGES = 8
_MAX_DECISION_CHARS = 2000
_MAX_ACTOR_CHARS = 200


@dataclass(frozen=True)
class WorkspaceChangeProposal:
    proposal_id: str
    plan_id: str
    decision_actor_id: str
    human_decision: str
    content_digest: str
    changes: tuple[FileChange, ...]
    requires_explicit_apply_approval: bool = True
    applied: bool = False


def prepare_workspace_change_proposal(
    *,
    review: EngineeringDecisionReview,
    decision_actor_id: str,
    human_decision: str,
    changes: tuple[FileChange, ...],
) -> WorkspaceChangeProposal:
    """Prepare a digest-bound proposal after an explicit human decision.

    The returned object is inert metadata. Applying it still requires the
    separate trusted-principal and digest-bound approval flow in
    ApprovedWorkspaceChanges.
    """
    if not isinstance(review, EngineeringDecisionReview):
        raise TypeError("review must be an EngineeringDecisionReview.")
    if review.review_status != "ready_for_human_decision":
        raise ValueError("decision review is blocked or not ready.")
    if review.quality.coverage_complete is not True:
        raise ValueError("structural quality coverage must be complete.")
    if review.disagreements:
        raise ValueError("unresolved specialist disagreements block proposal preparation.")
    if review.changes_authorized is not False or review.requires_human_review is not True:
        raise ValueError("review packet has an unsupported authorization state.")
    if not isinstance(decision_actor_id, str) or not decision_actor_id.strip() or len(decision_actor_id) > _MAX_ACTOR_CHARS or "\x00" in decision_actor_id:
        raise ValueError("decision_actor_id must be bounded non-empty text.")
    if not isinstance(human_decision, str) or not human_decision.strip() or len(human_decision) > _MAX_DECISION_CHARS or "\x00" in human_decision:
        raise ValueError("human_decision must be bounded non-empty text.")
    if not isinstance(changes, tuple) or not 1 <= len(changes) <= _MAX_CHANGES:
        raise ValueError("changes must be an immutable tuple containing 1-8 file changes.")

    seen_paths: set[str] = set()
    digest_items = []
    for change in changes:
        if not isinstance(change, FileChange):
            raise TypeError("changes must contain FileChange values.")
        if not isinstance(change.path, str) or not change.path.strip() or len(change.path) > 1000 or "\x00" in change.path:
            raise ValueError("change path must be bounded non-empty text.")
        if change.path in seen_paths:
            raise ValueError("change paths must be unique.")
        seen_paths.add(change.path)
        if not isinstance(change.expected_sha256, str) or len(change.expected_sha256) != 64 or any(c not in "0123456789abcdef" for c in change.expected_sha256):
            raise ValueError("expected_sha256 must be a lowercase SHA-256 digest.")
        if not isinstance(change.content, str) or "\x00" in change.content:
            raise ValueError("change content must be text without NUL bytes.")
        encoded = change.content.encode("utf-8")
        if len(encoded) > 256 * 1024:
            raise ValueError("individual proposed file exceeds the size limit.")
        digest_items.append({
            "path": change.path,
            "expected_sha256": change.expected_sha256,
            "new_sha256": hashlib.sha256(encoded).hexdigest(),
        })

    digest = hashlib.sha256(
        json.dumps(digest_items, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return WorkspaceChangeProposal(
        proposal_id="proposal_" + digest[:24],
        plan_id=review.plan_id,
        decision_actor_id=decision_actor_id.strip(),
        human_decision=human_decision.strip(),
        content_digest=digest,
        changes=changes,
    )
