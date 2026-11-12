"""Typed approval adapter for explicitly approved workspace edits.

This adapter normalizes the exact FileChange values used by
ApprovedWorkspaceChanges. It does not authorize or execute edits; callers must
persist the envelope with the approval request and verify it again immediately
before invoking the workspace executor.
"""
from __future__ import annotations

import re
from typing import Sequence

from automation.approved_workspace_changes import FileChange
from core.approval_operation import ApprovalOperation, canonical_operation_payload

_CAPABILITY = "workspace:write"
_OPERATION_TYPE = "workspace.write"
_TARGET = re.compile(r"^workspace:[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_MAX_CHANGES = 8
_MAX_FILE_BYTES = 240_000
_MAX_TOTAL_BYTES = 240_000


def workspace_change_payload(changes: Sequence[FileChange]) -> dict:
    """Return a strict, deterministic JSON payload for the exact proposed edits."""
    if not isinstance(changes, (tuple, list)) or not 1 <= len(changes) <= _MAX_CHANGES:
        raise ValueError("one to eight workspace file changes are required")
    payload_changes = []
    seen = set()
    total = 0
    for change in changes:
        if not isinstance(change, FileChange):
            raise ValueError("every workspace change must be a FileChange")
        if (not isinstance(change.path, str) or not change.path
                or len(change.path) > 1000 or "\x00" in change.path):
            raise ValueError("workspace path is invalid")
        if change.path in seen:
            raise ValueError("workspace change paths must be unique")
        seen.add(change.path)
        if (not isinstance(change.expected_sha256, str)
                or not _SHA256.fullmatch(change.expected_sha256)):
            raise ValueError("expected file digest is invalid")
        if not isinstance(change.content, str) or "\x00" in change.content:
            raise ValueError("workspace content must be text without NUL characters")
        encoded = change.content.encode("utf-8")
        if len(encoded) > _MAX_FILE_BYTES:
            raise ValueError("individual workspace change exceeds the 240 KB canonical payload budget")
        total += len(encoded)
        if total > _MAX_TOTAL_BYTES:
            raise ValueError("workspace change set exceeds the 240 KB canonical payload budget")
        payload_changes.append({
            "path": change.path,
            "expected_sha256": change.expected_sha256,
            "content": change.content,
        })
    payload = {"changes": payload_changes}
    canonical_operation_payload(payload)
    return payload


def build_workspace_approval_operation(
    *, workspace_id: str, changes: Sequence[FileChange],
) -> tuple[ApprovalOperation, dict]:
    """Build the exact typed envelope and normalized payload for approval."""
    if not isinstance(workspace_id, str) or not _TARGET.fullmatch(workspace_id):
        raise ValueError("workspace_id must be a stable workspace identifier")
    payload = workspace_change_payload(changes)
    operation = ApprovalOperation.from_payload(
        operation_type=_OPERATION_TYPE,
        capability=_CAPABILITY,
        target=workspace_id,
        payload=payload,
    )
    return operation, payload


def verify_workspace_approval_operation(
    operation: ApprovalOperation, *, workspace_id: str,
    changes: Sequence[FileChange],
) -> bool:
    """Verify operation identity and exact payload immediately before dispatch."""
    if not isinstance(operation, ApprovalOperation):
        return False
    if (operation.operation_type != _OPERATION_TYPE
            or operation.capability != _CAPABILITY
            or operation.target != workspace_id):
        return False
    try:
        payload = workspace_change_payload(changes)
        return operation.verify_payload(payload)
    except (TypeError, ValueError, UnicodeError):
        return False
