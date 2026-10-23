"""Proposal-only self-upgrade planning with explicit review boundaries.

This module does not write source files, execute tests, invoke subprocesses, or
promote changes. It creates a bounded digest of a proposed patch so trusted
application code can route it to existing approved workspace-change and test
services in a later, separately authorized workflow.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from typing import Sequence

from automation.approved_workspace_changes import FileChange
from automation.authorized_read_tools import ToolInputError

_MAX_CHANGES = 8
_MAX_FILE_BYTES = 256 * 1024
_MAX_TOTAL_BYTES = 1024 * 1024
_BLOCKED_PATH = re.compile(
    r"(^|/)(\.git|\.venv|venv|node_modules|__pycache__|\.ssh|\.aws|\.azure|\.config)(/|$)|"
    r"(^|/)(\.env($|\.)|[^/]*(secret|credential|password|token|private.?key|id_rsa|id_ed25519)[^/]*$)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class UpgradeProposal:
    proposal_id: str
    content_digest: str
    changed_paths: tuple[str, ...]
    patch_bytes: int
    review_required: bool = True
    tests_required: bool = True
    promotion_allowed: bool = False


class SelfUpgradeProposalBuilder:
    """Validate and fingerprint proposed file changes without applying them."""

    def __init__(self, allowed_paths: Sequence[str]) -> None:
        if not isinstance(allowed_paths, (tuple, list)) or not allowed_paths:
            raise ValueError("An explicit path allow-list is required.")
        if any(not isinstance(path, str) or not path or path.startswith("/") or ".." in path.split("/") for path in allowed_paths):
            raise ValueError("Allowed paths must be bounded relative paths.")
        self._allowed_paths = frozenset(allowed_paths)

    def build(self, changes: Sequence[FileChange]) -> UpgradeProposal:
        if not isinstance(changes, (tuple, list)) or not 1 <= len(changes) <= _MAX_CHANGES:
            raise ToolInputError("One to eight proposed changes are required.")
        seen: set[str] = set()
        total = 0
        manifest = []
        for change in changes:
            if not isinstance(change, FileChange):
                raise ToolInputError("Every proposal item must be a FileChange.")
            path = change.path
            if not isinstance(path, str) or path not in self._allowed_paths:
                raise ToolInputError("Proposed path is not explicitly allow-listed.")
            if path in seen:
                raise ToolInputError("Duplicate paths are not allowed.")
            if _BLOCKED_PATH.search(path.replace("\\", "/")):
                raise ToolInputError("Sensitive or generated paths are excluded.")
            if not re.fullmatch(r"[0-9a-f]{64}", change.expected_sha256 or ""):
                raise ToolInputError("Expected source SHA-256 digest is invalid.")
            if not isinstance(change.content, str) or "\x00" in change.content:
                raise ToolInputError("Proposed content must be text without NUL bytes.")
            encoded = change.content.encode("utf-8")
            if len(encoded) > _MAX_FILE_BYTES:
                raise ToolInputError("A proposed file exceeds the size limit.")
            total += len(encoded)
            if total > _MAX_TOTAL_BYTES:
                raise ToolInputError("Total proposed patch exceeds the size limit.")
            seen.add(path)
            manifest.append({
                "path": path,
                "expected_sha256": change.expected_sha256,
                "proposed_sha256": hashlib.sha256(encoded).hexdigest(),
                "bytes": len(encoded),
            })
        canonical = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
        digest = hashlib.sha256(canonical).hexdigest()
        return UpgradeProposal(
            proposal_id="upgrade_" + digest[:24],
            content_digest=digest,
            changed_paths=tuple(sorted(seen)),
            patch_bytes=total,
        )
