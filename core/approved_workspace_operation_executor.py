"""Claim-fenced execution bridge for typed, owner-approved workspace edits.

This is deliberately capability-specific. It accepts only the workspace.write
contract, reconstructs the exact payload from FileChange objects, consumes the
matching central approval once, and rechecks the live durable claim at the
executor's final pre-commit boundary.
"""
from __future__ import annotations

from typing import Callable, Sequence

from automation.approved_workspace_changes import (
    ApprovedWorkspaceChanges, ChangeResult, FileChange,
)
from automation.authorized_read_tools import ToolPrincipal
from core.capability_approval_gateway import CapabilityApprovalGateway
from core.tool_policy import PolicyRequest
from core.workspace_approval_operation import build_workspace_approval_operation


def apply_claim_bound_workspace_changes(
    *,
    gateway: CapabilityApprovalGateway,
    executor: ApprovedWorkspaceChanges,
    request: PolicyRequest,
    approval_token: str,
    principal: ToolPrincipal,
    workspace_id: str,
    changes: Sequence[FileChange],
    validate_live_claim: Callable[[], bool],
) -> ChangeResult:
    """Consume an exact typed approval and apply only while its worker claim lives.

    The executor's own trusted-principal, path allowlist, expected-file digest,
    audit and rollback controls remain mandatory. This bridge does not grant
    general tool authority and does not accept caller-supplied operation JSON.
    """
    if not isinstance(gateway, CapabilityApprovalGateway):
        raise TypeError("a configured central approval gateway is required")
    if not isinstance(executor, ApprovedWorkspaceChanges):
        raise TypeError("an approved workspace executor is required")
    if not isinstance(request, PolicyRequest):
        raise TypeError("a validated policy request is required")
    if not isinstance(principal, ToolPrincipal):
        raise TypeError("a trusted workspace principal is required")
    if principal.actor_id != request.principal_id:
        raise PermissionError("workspace principal does not match the approved request")
    if not callable(validate_live_claim):
        raise TypeError("a live durable-claim validator is required")

    def claim_is_live() -> bool:
        try:
            return validate_live_claim() is True
        except Exception:
            return False

    if not claim_is_live():
        raise PermissionError("durable worker claim is no longer valid")
    operation, payload = build_workspace_approval_operation(
        workspace_id=workspace_id, changes=changes,
    )
    if not operation.verify_policy_request(request):
        raise PermissionError("workspace operation does not match the approved policy request")
    decision = gateway.authorize(
        request, approval_token=approval_token, operation=operation,
    )
    if getattr(decision, "allowed", False) is not True or (
        getattr(decision, "approval_consumed", False) is not True
    ):
        raise PermissionError("exact typed workspace approval was not consumed")
    if not claim_is_live():
        raise PermissionError("durable worker claim changed during approval consumption")
    # Reconstruct from the canonical payload so executor input is precisely what
    # was bound to the approval digest, not a mutable caller-owned sequence.
    exact_changes = tuple(
        FileChange(
            path=item["path"],
            expected_sha256=item["expected_sha256"],
            content=item["content"],
        )
        for item in payload["changes"]
    )
    return executor.apply(
        principal=principal,
        changes=exact_changes,
        execution_guard=claim_is_live,
    )
