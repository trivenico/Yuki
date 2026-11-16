"""Opt-in composition root for durable, approval-bound sandbox execution.

This constructs security stores and a dedicated worker, but deliberately does
not start a polling loop or expose an HTTP execution endpoint.
"""
from __future__ import annotations

from pathlib import Path
import re
from typing import Any

from automation.execution_task import DurableSandboxExecutionWorker
from automation.sandbox_execution_submission import SandboxExecutionSubmissionService
from automation.sandbox_execution_approval import (
    SQLiteSandboxExecutionApprovalStore,
    SandboxExecutionAuditWriter,
)
from core.approval_audit import SQLiteApprovalAuditLog
from core.auth import BuiltInAuthService
from core.durable_task_engine import DurableTaskEngine
from core.sqlite_file_security import prepare_private_sqlite_path
from hackback.sandbox import DockerSandbox


class SandboxExecutionRuntime:
    """Own the durable stores and worker for one configured sandbox profile."""

    def __init__(self, *, engine: DurableTaskEngine,
                 approval_store: SQLiteSandboxExecutionApprovalStore,
                 audit_log: SQLiteApprovalAuditLog,
                 sandbox: DockerSandbox,
                 worker: DurableSandboxExecutionWorker,\n                 submission_service: SandboxExecutionSubmissionService):
        self.engine = engine
        self.approval_store = approval_store
        self.audit_log = audit_log
        self.sandbox = sandbox
        self.worker = worker
        self._closed = False

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self.engine.close()


def build_sandbox_execution_runtime(
    config: dict[str, Any], *, auth_service: BuiltInAuthService,
) -> SandboxExecutionRuntime | None:
    """Build only when explicitly enabled; incomplete opt-in fails closed.

    The Docker approval callback is a private adapter: the durable worker has
    already verified and atomically consumed the exact human approval before
    calling DockerSandbox.run. No model- or API-supplied boolean reaches it.
    """
    if not isinstance(config, dict):
        raise TypeError("config must be a dictionary")
    enabled = config.get("sandbox_execution_enabled", False)
    if type(enabled) is not bool:
        raise ValueError("sandbox_execution_enabled must be a boolean")
    if not enabled:
        return None
    if not isinstance(auth_service, BuiltInAuthService):
        raise TypeError("auth_service must be a BuiltInAuthService when sandbox execution is enabled")

    workspace_value = config.get("workspace")
    if not isinstance(workspace_value, str) or not workspace_value.strip():
        raise ValueError("sandbox execution requires a configured workspace")
    root = Path(workspace_value).expanduser().resolve()
    workspace_setting = config.get(
        "sandbox_execution_workspace", str(root / "sandbox-execution-workspace")
    )
    if not isinstance(workspace_setting, str) or not workspace_setting.strip():
        raise ValueError("sandbox execution workspace must be a non-empty path")
    sandbox_workspace = Path(workspace_setting).expanduser().resolve()
    if sandbox_workspace == Path(sandbox_workspace.anchor) or sandbox_workspace == root:
        raise ValueError("sandbox execution requires a dedicated non-root workspace")
    if "," in str(sandbox_workspace):
        raise ValueError("sandbox execution workspace contains unsupported mount syntax")

    images = config.get("sandbox_execution_allowed_images")
    if not isinstance(images, list) or not images:
        raise ValueError("sandbox execution requires digest-pinned allow-listed images")
    if any(not isinstance(image, str) or len(image) > 255
           or re.fullmatch(r"[^@\s]+@sha256:[0-9a-f]{64}", image) is None
           for image in images) or len(set(images)) != len(images):
        raise ValueError("sandbox execution image allow-list is invalid")

    database_root = root / "sandbox-execution-state"
    if (sandbox_workspace == database_root
            or database_root in sandbox_workspace.parents
            or sandbox_workspace in database_root.parents):
        raise ValueError("sandbox workspace must be isolated from private execution state")
    task_db = prepare_private_sqlite_path(database_root / "tasks.sqlite3")
    audit_db = prepare_private_sqlite_path(database_root / "audit.sqlite3")
    auth_db = str(Path(auth_service.database_path).resolve())
    if len({task_db, audit_db, auth_db}) != 3:
        raise ValueError("sandbox execution requires separate task, audit and authentication databases")

    worker_id = config.get("sandbox_execution_worker_id", "yuki-sandbox-worker")
    if not isinstance(worker_id, str) or not worker_id.strip() or len(worker_id) > 256:
        raise ValueError("sandbox execution worker ID must be bounded non-empty text")

    engine = DurableTaskEngine(task_db)
    try:
        approval_store = SQLiteSandboxExecutionApprovalStore(
            auth_db, auth_service=auth_service,
        )
        audit_log = SQLiteApprovalAuditLog(audit_db)
        audit_writer = SandboxExecutionAuditWriter(audit_log)
        sandbox_workspace.mkdir(parents=True, exist_ok=True, mode=0o700)
        sandbox = DockerSandbox(
            sandbox_workspace,
            allowed_images=tuple(images),
            enabled=True,
            # Approval is enforced by the worker's trusted store immediately
            # before dispatch; this callback is intentionally not user-facing.
            approver=lambda _description: True,
        )
        worker = DurableSandboxExecutionWorker.from_trusted_stores(
            engine,
            worker_id=worker_id.strip(),
            sandbox=sandbox,
            approval_store=approval_store,
            audit_writer=audit_writer,
        )
        return SandboxExecutionRuntime(
            engine=engine, approval_store=approval_store,
            audit_log=audit_log, sandbox=sandbox, worker=worker,
        )
    except Exception:
        engine.close()
        raise
