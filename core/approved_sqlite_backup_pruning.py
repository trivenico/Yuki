"""Authenticated, single-use approval boundary for SQLite backup pruning."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from typing import Callable

from automation.authorized_read_tools import ToolPrincipal, ToolAuthorizationError
from core.approval_audit import SQLiteApprovalAuditLog
from core.backup_inventory import SQLiteDatabaseEntry
from core.pending_approvals import SQLitePendingApprovalStore
from core.sqlite_backup_retention import (
    SQLiteBackupRetentionError,
    plan_sqlite_backup_retention,
    prune_sqlite_backup_sets,
)
from core.sqlite_backup_verify import verify_sqlite_backup_set


class BackupPruneApprovalError(RuntimeError):
    """Approval or audit preconditions prevent a safe backup prune."""


class BackupPruneOutcomeUnknown(BackupPruneApprovalError):
    """Pruning may have happened, but its final audit outcome was not persisted."""


@dataclass(frozen=True)
class BackupPruneApproval:
    pending_id: str
    operation_fingerprint: str
    approved_set_ids: tuple[str, ...]


def _stat_identity(path: Path) -> dict[str, int]:
    info = path.stat(follow_symlinks=False)
    if stat.S_ISLNK(info.st_mode):
        raise BackupPruneApprovalError("symlink_in_operation_inventory")
    return {
        "device": info.st_dev, "inode": info.st_ino, "mode": info.st_mode,
        "size": info.st_size, "mtime_ns": info.st_mtime_ns,
        "ctime_ns": info.st_ctime_ns,
    }


def _operation_fingerprint(
    backup_root: str | Path,
    expected_entries: tuple[SQLiteDatabaseEntry, ...],
    *,
    workspace_root: str | Path,
    keep_last: int,
    pinned_set_ids: tuple[str, ...],
    approved_set_ids: tuple[str, ...],
) -> str:
    root = Path(backup_root).expanduser().resolve(strict=True)
    workspace = Path(workspace_root).expanduser().resolve(strict=True)
    plan = plan_sqlite_backup_retention(
        root, expected_entries, workspace_root=workspace,
        keep_last=keep_last, pinned_set_ids=pinned_set_ids,
    )
    if not approved_set_ids or len(set(approved_set_ids)) != len(approved_set_ids):
        raise BackupPruneApprovalError("nonempty_unique_approved_set_ids_required")
    if not set(approved_set_ids).issubset(plan.eligible_set_ids):
        raise BackupPruneApprovalError("approved_sets_not_currently_eligible")

    manifests = {}
    for set_id in plan.verified_set_ids:
        manifest_path = root / set_id / "manifest.json"
        manifests[set_id] = hashlib.sha256(manifest_path.read_bytes()).hexdigest()

    inventory = []
    seen_names = set()
    seen_paths = set()
    for entry in expected_entries:
        if not isinstance(entry, SQLiteDatabaseEntry):
            raise BackupPruneApprovalError("invalid_database_inventory_entry")
        if entry.name in seen_names:
            raise BackupPruneApprovalError("duplicate_database_inventory_name")
        source = Path(entry.path).expanduser()
        if source.is_symlink():
            raise BackupPruneApprovalError("database_inventory_symlink")
        source = source.resolve(strict=True)
        if source in seen_paths or not source.is_file():
            raise BackupPruneApprovalError("invalid_database_inventory_path")
        seen_names.add(entry.name)
        seen_paths.add(source)
        inventory.append({
            "name": entry.name,
            "path_sha256": hashlib.sha256(str(source).encode("utf-8")).hexdigest(),
            "identity": _stat_identity(source),
        })

    payload = {
        "version": 1,
        "backup_root_path_sha256": hashlib.sha256(str(root).encode("utf-8")).hexdigest(),
        "backup_root_identity": _stat_identity(root),
        "workspace_path_sha256": hashlib.sha256(str(workspace).encode("utf-8")).hexdigest(),
        "inventory": inventory,
        "keep_last": keep_last,
        "pinned_set_ids": list(pinned_set_ids),
        "verified_set_ids": list(plan.verified_set_ids),
        "retained_set_ids": list(plan.retained_set_ids),
        "eligible_set_ids": list(plan.eligible_set_ids),
        "approved_set_ids": list(approved_set_ids),
        "manifest_sha256": manifests,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return hashlib.sha256(encoded).hexdigest()


class ApprovedSQLiteBackupPruner:
    """Orchestrate authenticated, actor/session-bound, one-time prune approvals."""

    def __init__(
        self,
        *,
        pending_store: SQLitePendingApprovalStore,
        audit_log: SQLiteApprovalAuditLog,
        trusted_principal: Callable[[ToolPrincipal], bool],
        approve_operation: Callable[[str, str, str], bool],
    ):
        if not isinstance(pending_store, SQLitePendingApprovalStore):
            raise TypeError("trusted_pending_approval_store_required")
        if not isinstance(audit_log, SQLiteApprovalAuditLog):
            raise TypeError("trusted_sqlite_approval_audit_required")
        if not callable(trusted_principal) or not callable(approve_operation):
            raise TypeError("trusted_principal_and_approval_callbacks_required")
        self._pending = pending_store
        self._audit = audit_log
        self._trusted = trusted_principal
        self._approve = approve_operation

    @staticmethod
    def _validate_principal(principal: ToolPrincipal, trusted: Callable, session_id: str) -> None:
        if not isinstance(principal, ToolPrincipal):
            raise ToolAuthorizationError("trusted_authenticated_principal_required")
        try:
            accepted = trusted(principal) is True
        except Exception:
            accepted = False
        if (not accepted or not isinstance(principal.actor_id, str)
                or not principal.actor_id.strip() or len(principal.actor_id) > 200
                or not isinstance(principal.permissions, frozenset)
                or "backup:prune" not in principal.permissions):
            raise ToolAuthorizationError("backup_prune_permission_denied")
        if not isinstance(session_id, str) or not session_id.strip() or len(session_id) > 256:
            raise ToolAuthorizationError("valid_authenticated_session_required")

    def approve(
        self,
        *,
        principal: ToolPrincipal,
        session_id: str,
        backup_root: str | Path,
        expected_entries: tuple[SQLiteDatabaseEntry, ...],
        workspace_root: str | Path,
        keep_last: int,
        approved_set_ids: tuple[str, ...],
        pinned_set_ids: tuple[str, ...] = (),
    ) -> BackupPruneApproval:
        self._validate_principal(principal, self._trusted, session_id)
        fingerprint = _operation_fingerprint(
            backup_root, expected_entries, workspace_root=workspace_root,
            keep_last=keep_last, pinned_set_ids=pinned_set_ids,
            approved_set_ids=approved_set_ids,
        )
        pending = self._pending.put(
            "backup.prune",
            {"operation_fingerprint": fingerprint},
            {"operation_fingerprint": fingerprint, "approved_set_ids": list(approved_set_ids)},
            actor=principal.actor_id, session_id=session_id,
        )
        audit_skill = "backup-prune:" + fingerprint
        try:
            self._audit.append(event_type="requested", pending_id=pending.pending_id,
                               actor=principal.actor_id, skill_id=audit_skill)
        except Exception as exc:
            self._pending.deny(pending.pending_id)
            raise BackupPruneApprovalError("requested_audit_failed_prune_denied") from exc

        try:
            accepted = self._approve(principal.actor_id, pending.pending_id, fingerprint) is True
        except Exception:
            accepted = False
        if not accepted:
            try:
                self._audit.append(event_type="denied", pending_id=pending.pending_id,
                                   actor=principal.actor_id, skill_id=audit_skill)
            finally:
                self._pending.deny(pending.pending_id)
            raise ToolAuthorizationError("exact_backup_prune_approval_denied")

        try:
            self._audit.append(event_type="approved", pending_id=pending.pending_id,
                               actor=principal.actor_id, skill_id=audit_skill)
        except Exception as exc:
            self._pending.deny(pending.pending_id)
            raise BackupPruneApprovalError("approval_audit_failed_prune_denied") from exc
        return BackupPruneApproval(pending.pending_id, fingerprint, approved_set_ids)

    def execute(
        self,
        *,
        principal: ToolPrincipal,
        session_id: str,
        approval: BackupPruneApproval,
        backup_root: str | Path,
        expected_entries: tuple[SQLiteDatabaseEntry, ...],
        workspace_root: str | Path,
        keep_last: int,
        pinned_set_ids: tuple[str, ...] = (),
    ) -> tuple[str, ...]:
        self._validate_principal(principal, self._trusted, session_id)
        if not isinstance(approval, BackupPruneApproval):
            raise BackupPruneApprovalError("valid_backup_prune_approval_required")
        pending = self._pending.consume(
            approval.pending_id, actor=principal.actor_id, session_id=session_id,
        )
        if pending is None:
            raise BackupPruneApprovalError("approval_missing_expired_or_already_consumed")
        expected_action = {
            "operation_fingerprint": approval.operation_fingerprint,
            "approved_set_ids": list(approval.approved_set_ids),
        }
        if (pending.skill_id != "backup.prune" or pending.action != expected_action
                or pending.arguments != {"operation_fingerprint": approval.operation_fingerprint}):
            raise BackupPruneApprovalError("approval_snapshot_mismatch")

        current = _operation_fingerprint(
            backup_root, expected_entries, workspace_root=workspace_root,
            keep_last=keep_last, pinned_set_ids=pinned_set_ids,
            approved_set_ids=approval.approved_set_ids,
        )
        if current != approval.operation_fingerprint:
            raise BackupPruneApprovalError("operation_changed_reapproval_required")

        audit_skill = "backup-prune:" + current
        self._audit.append(event_type="execution_started", pending_id=approval.pending_id,
                           actor=principal.actor_id, skill_id=audit_skill)
        try:
            removed = prune_sqlite_backup_sets(
                backup_root, expected_entries, workspace_root=workspace_root,
                keep_last=keep_last, approved_set_ids=approval.approved_set_ids,
                explicit_approval=True, pinned_set_ids=pinned_set_ids,
            )
        except Exception as exc:
            try:
                self._audit.append(event_type="execution_failed", pending_id=approval.pending_id,
                                   actor=principal.actor_id, skill_id=audit_skill)
            except Exception as audit_exc:
                raise BackupPruneOutcomeUnknown("prune_failed_and_failure_audit_unavailable") from audit_exc
            raise
        try:
            self._audit.append(event_type="executed", pending_id=approval.pending_id,
                               actor=principal.actor_id, skill_id=audit_skill)
        except Exception as exc:
            raise BackupPruneOutcomeUnknown("prune_completed_but_success_audit_unavailable") from exc
        return removed
