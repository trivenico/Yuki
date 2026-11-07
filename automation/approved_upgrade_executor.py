"""Digest-bound handoff from reviewed Yuki upgrades to workspace edits.

This adapter is deliberately not a sandbox. It applies only the exact persisted
snapshot after proposal review, trusted-principal validation, source-hash
preconditions, and a second execution approval bound to both proposal and
workspace-change digests.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

from automation.approved_workspace_changes import (
    ApprovedWorkspaceChanges,
    ChangeResult,
)
from automation.authorized_read_tools import ToolPrincipal
from automation.self_upgrade_pipeline import SelfUpgradeReviewStore


@dataclass(frozen=True)
class UpgradeExecutionResult:
    proposal_id: str
    proposal_digest: str
    change_result: ChangeResult


class ApprovedUpgradeExecutor:
    """Apply a reviewed snapshot only after a second, digest-bound approval."""

    def __init__(
        self,
        review_store: SelfUpgradeReviewStore,
        workspace: str | Path,
        *,
        trusted_principal: Callable[[ToolPrincipal], bool],
        approve_execution: Callable[[str, str, str, str, str], bool],
        audit: Callable[[dict], None],
        allowed_paths: Sequence[str],
        staging_workspace_validator: Callable[[Path], bool],
    ) -> None:
        if not isinstance(review_store, SelfUpgradeReviewStore):
            raise TypeError("review_store must be a SelfUpgradeReviewStore")
        if (not callable(trusted_principal) or not callable(approve_execution)
                or not callable(audit) or not callable(staging_workspace_validator)):
            raise TypeError("trusted-principal, execution-approval, audit, and staging-workspace validation callbacks are required")
        if not isinstance(allowed_paths, (tuple, list)) or not allowed_paths:
            raise ValueError("an explicit workspace path allow-list is required")
        self.review_store = review_store
        self.workspace = Path(workspace).expanduser().resolve()
        if not self.workspace.is_dir():
            raise ValueError("workspace must be an existing directory")
        try:
            staging_allowed = staging_workspace_validator(self.workspace) is True
        except Exception:
            staging_allowed = False
        if not staging_allowed:
            raise PermissionError("workspace is not an approved disposable staging checkout")
        self.trusted_principal = trusted_principal
        self.approve_execution = approve_execution
        self.audit = audit
        self.allowed_paths = frozenset(allowed_paths)

    def execute(
        self, proposal_id: str, *, reviewer: str, principal: ToolPrincipal
    ) -> UpgradeExecutionResult:
        if not isinstance(principal, ToolPrincipal) or not isinstance(reviewer, str):
            raise PermissionError("authenticated principal and reviewer identity are required")
        if not reviewer.strip() or reviewer.strip() != principal.actor_id:
            raise PermissionError("reviewer and execution principal must be the same identity")
        snapshot = self.review_store.get_approved_snapshot(proposal_id, reviewer=reviewer)
        files = snapshot["files"]
        if not files or any(change.path not in self.allowed_paths for change in files):
            raise PermissionError("proposal contains a path outside the executor allow-list")

        def approval_bridge(actor_id: str, change_id: str, workspace_digest: str) -> bool:
            try:
                return self.approve_execution(
                    proposal_id, snapshot["content_digest"], actor_id,
                    change_id, workspace_digest,
                ) is True
            except Exception:
                return False

        def audit_bridge(event: dict) -> None:
            enriched = dict(event)
            enriched["proposal_id"] = proposal_id
            enriched["proposal_digest"] = snapshot["content_digest"]
            self.audit(enriched)

        workspace_changes = ApprovedWorkspaceChanges(
            self.workspace,
            trusted_principal=self.trusted_principal,
            approve_change=approval_bridge,
            audit=audit_bridge,
            allowed_paths=tuple(sorted(self.allowed_paths)),
        )
        result = workspace_changes.apply(principal=principal, changes=files)
        return UpgradeExecutionResult(
            proposal_id=proposal_id,
            proposal_digest=snapshot["content_digest"],
            change_result=result,
        )
