"""Render bounded, digest-verified unified diffs for inert workspace proposals.

No filesystem access, Git command, code execution, or approval is performed here.
Callers supply the base file contents obtained through an authorized read path.
"""
from __future__ import annotations

from dataclasses import dataclass
import difflib
import hashlib
import json
from typing import Mapping

from automation.workspace_change_proposal import WorkspaceChangeProposal

_MAX_DIFF_BYTES = 1024 * 1024
_MAX_BASE_FILE_BYTES = 256 * 1024


@dataclass(frozen=True)
class FilePatchReview:
    path: str
    old_sha256: str
    new_sha256: str
    unified_diff: str
    additions: int
    deletions: int


@dataclass(frozen=True)
class WorkspacePatchReview:
    proposal_id: str
    content_digest: str
    files: tuple[FilePatchReview, ...]
    total_additions: int
    total_deletions: int
    diff_bytes: int
    ready_for_human_review: bool = True
    applied: bool = False


def render_workspace_patch_review(
    proposal: WorkspaceChangeProposal,
    base_contents: Mapping[str, str],
) -> WorkspacePatchReview:
    """Build a deterministic unified diff after verifying every base digest."""
    if not isinstance(proposal, WorkspaceChangeProposal):
        raise TypeError("proposal must be a WorkspaceChangeProposal.")
    if proposal.applied is not False or proposal.requires_explicit_apply_approval is not True:
        raise ValueError("only unapplied proposals requiring explicit approval can be reviewed.")
    if not isinstance(base_contents, Mapping):
        raise TypeError("base_contents must be a path-to-text mapping.")
    if set(base_contents) != {change.path for change in proposal.changes}:
        raise ValueError("base contents must match the proposal paths exactly.")

    digest_items = []
    reviews = []
    total_bytes = 0
    additions = deletions = 0
    for change in proposal.changes:
        old = base_contents[change.path]
        if not isinstance(old, str) or "\x00" in old:
            raise ValueError("base file content must be text without NUL bytes.")
        old_bytes = old.encode("utf-8")
        new_bytes = change.content.encode("utf-8")
        if len(old_bytes) > _MAX_BASE_FILE_BYTES:
            raise ValueError("base file exceeds the review size limit.")
        old_digest = hashlib.sha256(old_bytes).hexdigest()
        if old_digest != change.expected_sha256:
            raise ValueError(f"base file digest mismatch for {change.path}.")
        new_digest = hashlib.sha256(new_bytes).hexdigest()
        digest_items.append({
            "path": change.path,
            "expected_sha256": change.expected_sha256,
            "new_sha256": new_digest,
        })
        diff_lines = list(difflib.unified_diff(
            old.splitlines(keepends=True),
            change.content.splitlines(keepends=True),
            fromfile=f"a/{change.path}",
            tofile=f"b/{change.path}",
            lineterm="\n",
        ))
        diff_text = "".join(diff_lines)
        encoded_diff = diff_text.encode("utf-8")
        total_bytes += len(encoded_diff)
        if total_bytes > _MAX_DIFF_BYTES:
            raise ValueError("combined unified diff exceeds the review size limit.")
        file_additions = sum(1 for line in diff_lines if line.startswith("+") and not line.startswith("+++"))
        file_deletions = sum(1 for line in diff_lines if line.startswith("-") and not line.startswith("---"))
        additions += file_additions
        deletions += file_deletions
        reviews.append(FilePatchReview(
            path=change.path,
            old_sha256=old_digest,
            new_sha256=new_digest,
            unified_diff=diff_text,
            additions=file_additions,
            deletions=file_deletions,
        ))

    actual_digest = hashlib.sha256(
        json.dumps(digest_items, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    if actual_digest != proposal.content_digest:
        raise ValueError("proposal content digest does not match its file changes.")
    return WorkspacePatchReview(
        proposal_id=proposal.proposal_id,
        content_digest=actual_digest,
        files=tuple(reviews),
        total_additions=additions,
        total_deletions=deletions,
        diff_bytes=total_bytes,
    )
