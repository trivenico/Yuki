"""Prepare immutable, reviewable workspace-change proposals without applying them.

This module bridges a completed human decision review to the existing
ApprovedWorkspaceChanges service. It does not write files, execute tests, or
treat proposal creation as authorization.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from pathlib import PurePosixPath

from automation.approved_workspace_changes import FileChange
from automation.decision_review import EngineeringDecisionReview

_MAX_CHANGES = 8
_MAX_FILE_BYTES = 256 * 1024
_MAX_PATCH_BYTES = 1024 * 1024
_MAX_DECISION_CHARS = 2000
_MAX_ACTOR_CHARS = 200
_BLOCKED_PARTS = frozenset({".git", ".venv", "venv", "node_modules", "__pycache__", ".ssh", ".aws", ".azure", ".config"})
_SECRET_PART = re.compile(r"(^\.env($|\.)|secret|credential|password|token|private.?key|id_rsa|id_ed25519)", re.I)


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


def _validate_relative_path(path: str) -> None:
    if not isinstance(path, str) or not path.strip() or len(path) > 1000 or "\x00" in path or "\\" in path:
        raise ValueError("change path must be bounded relative POSIX text.")
    candidate = PurePosixPath(path)
    if candidate.is_absolute() or any(part in {"", ".", ".."} for part in candidate.parts):
        raise ValueError("absolute paths, dot segments, and parent traversal are not allowed.")
    if any(part in _BLOCKED_PARTS or _SECRET_PART.search(part) for part in candidate.parts):
        raise ValueError("change path is excluded by workspace proposal policy.")


def prepare_workspace_change_proposal(
    *,
    review: EngineeringDecisionReview,
    decision_actor_id: str,
    human_decision: str,
    changes: tuple[FileChange, ...],
) -> WorkspaceChangeProposal:
    """Prepare a digest-bound proposal after a human decision.

    The returned object is inert metadata. Applying it still requires the
    separate trusted-principal and digest-bound approval flow in
    ApprovedWorkspaceChanges. decision_actor_id is attribution supplied by
    the caller, not an authentication credential.
    """
    if not isinstance(review, EngineeringDecisionReview):
        raise TypeError("review must be an EngineeringDecisionReview.")
    if review.review_status != "ready_for_human_decision":
        raise ValueError("decision review is blocked or not ready.")
    if review.quality.plan_id != review.plan_id or review.quality.coverage_complete is not True:
        raise ValueError("decision review quality must match the plan and have complete coverage.")
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
    total_bytes = 0
    for change in changes:
        if not isinstance(change, FileChange):
            raise TypeError("changes must contain FileChange values.")
        _validate_relative_path(change.path)
        if change.path in seen_paths:
            raise ValueError("change paths must be unique.")
        seen_paths.add(change.path)
        if not isinstance(change.expected_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", change.expected_sha256):
            raise ValueError("expected_sha256 must be a lowercase SHA-256 digest.")
        if not isinstance(change.content, str) or "\x00" in change.content:
            raise ValueError("change content must be text without NUL bytes.")
        encoded = change.content.encode("utf-8")
        if len(encoded) > _MAX_FILE_BYTES:
            raise ValueError("individual proposed file exceeds the size limit.")
        total_bytes += len(encoded)
        if total_bytes > _MAX_PATCH_BYTES:
            raise ValueError("combined proposed changes exceed the patch size limit.")
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
