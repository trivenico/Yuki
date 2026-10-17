"""Local approval runtime construction.

This module centralizes storage paths and process-local CLI identity. It does not
authenticate remote callers and must not be reused as an API identity mechanism.
"""
from __future__ import annotations

from dataclasses import dataclass
import getpass
import os
from pathlib import Path
import secrets

from core.approval_audit import SQLiteApprovalAuditLog
from core.approval_flow import ApprovalCoordinator
from core.pending_approvals import SQLitePendingApprovalStore


@dataclass(frozen=True)
class LocalApprovalRuntime:
    coordinator: ApprovalCoordinator
    actor: str
    session_id: str


def build_local_approval_runtime(workspace: str | Path, *, ttl_seconds: int = 300,
                                 max_pending: int = 128) -> LocalApprovalRuntime:
    """Create a local CLI approval coordinator with private workspace state.

    Actor is the OS account name and session_id is fresh for this process. This
    deliberately prevents a pending approval from being resumed in a later process
    until a proper authenticated persistent-session design exists.
    """
    root = Path(workspace).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    state_dir = root / ".yuki" / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    if os.name == "posix":
        try:
            state_dir.chmod(0o700)
        except OSError:
            # Do not silently relax to a shared directory; check actual permissions.
            if state_dir.stat().st_mode & 0o077:
                raise PermissionError("Approval state directory must be private (mode 0700).")

    pending = SQLitePendingApprovalStore(
        str(state_dir / "pending-approvals.sqlite3"),
        ttl_seconds=ttl_seconds,
        max_pending=max_pending,
    )
    audit = SQLiteApprovalAuditLog(str(state_dir / "approval-audit.sqlite3"))
    for path in (pending.database_path, audit.database_path):
        db_path = Path(path)
        if os.name == "posix" and db_path.exists():
            try:
                db_path.chmod(0o600)
            except OSError:
                if db_path.stat().st_mode & 0o077:
                    raise PermissionError("Approval database must be private (mode 0600).")

    actor = getpass.getuser().strip()
    if not actor:
        raise RuntimeError("Could not determine local OS user for approval identity.")
    session_id = secrets.token_urlsafe(32)
    return LocalApprovalRuntime(
        coordinator=ApprovalCoordinator(
            max_pending=max_pending,
            pending_store=pending,
            audit_log=audit,
        ),
        actor=actor,
        session_id=session_id,
    )
