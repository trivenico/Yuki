"""Production composition for the opt-in durable Docker sandbox recovery path.

This module intentionally composes recovery, not artifact promotion approvals.
The deployment must still expose job lifecycle operations only through a
separately reviewed, authenticated application service.
"""
from __future__ import annotations

import os
from pathlib import Path
import re
import shutil
import stat
from typing import Any

from automation.docker_job_runtime import DockerJobRuntime
from automation.sandbox_job_recovery import SandboxJobRecovery
from automation.sandbox_job_store import SandboxJobStore
from core.recovery_audit import RecoveryAuditLog


class SandboxJobCompositionError(RuntimeError):
    """Durable sandbox composition is incomplete or unsafe."""


def build_sandbox_job_recovery(config: dict[str, Any]) -> SandboxJobRecovery | None:
    """Build the mandatory recovery coordinator when durable jobs are enabled.

    The jobs root must be provisioned by the operator before startup, owned by
    the service UID, and mode 0700. No directory or Docker daemon is created or
    modified by this factory. Disabled mode does not touch Docker or SQLite.
    """
    if not isinstance(config, dict):
        raise TypeError("config must be a dictionary")
    enabled = config.get("sandbox_jobs_enabled", False)
    if type(enabled) is not bool:
        raise SandboxJobCompositionError("sandbox_jobs_enabled must be boolean")
    if not enabled:
        return None

    root_value = config.get("sandbox_jobs_root")
    image = config.get("sandbox_jobs_image")
    command = config.get("sandbox_jobs_command")
    docker_binary_value = config.get("sandbox_jobs_docker_binary", "docker")
    workspace_value = config.get("workspace")
    if not isinstance(root_value, str) or not root_value.strip():
        raise SandboxJobCompositionError("sandbox_jobs_root is required when durable jobs are enabled")
    if not isinstance(image, str) or not image.strip():
        raise SandboxJobCompositionError("sandbox_jobs_image is required when durable jobs are enabled")
    if not isinstance(command, list) or not command or any(
        not isinstance(part, str) or not part or "\x00" in part or len(part) > 500
        for part in command
    ):
        raise SandboxJobCompositionError("sandbox_jobs_command must be a fixed non-empty argv list")
    if not isinstance(docker_binary_value, str) or not docker_binary_value.strip():
        raise SandboxJobCompositionError("sandbox_jobs_docker_binary must be non-empty text")
    if not isinstance(workspace_value, str) or not workspace_value.strip():
        raise SandboxJobCompositionError("workspace is required for durable sandbox jobs")

    root = Path(root_value).expanduser()
    if root.is_symlink():
        raise SandboxJobCompositionError("sandbox jobs root cannot be a symlink")
    try:
        root = root.resolve(strict=True)
        root_info = root.stat()
    except OSError as exc:
        raise SandboxJobCompositionError("sandbox jobs root must already exist") from exc
    if not stat.S_ISDIR(root_info.st_mode) or root_info.st_uid != os.getuid():
        raise SandboxJobCompositionError("sandbox jobs root must be a directory owned by the service user")
    if stat.S_IMODE(root_info.st_mode) != 0o700:
        raise SandboxJobCompositionError("sandbox jobs root must have mode 0700")

    workspace = Path(workspace_value).expanduser()
    try:
        workspace = workspace.resolve(strict=True)
    except OSError as exc:
        raise SandboxJobCompositionError("sandbox workspace must already exist") from exc
    if not workspace.is_dir():
        raise SandboxJobCompositionError("sandbox workspace must be a directory")

    docker_binary = shutil.which(docker_binary_value)
    if docker_binary is None:
        raise SandboxJobCompositionError("configured Docker executable is unavailable")

    database_path = root / "sandbox-jobs.sqlite3"
    audit_path = root / "sandbox-recovery-audit.sqlite3"
    for path in (database_path, audit_path):
        if path.is_symlink():
            raise SandboxJobCompositionError("sandbox database files cannot be symlinks")
        if path.exists():
            info = path.lstat()
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                    or info.st_nlink != 1):
                raise SandboxJobCompositionError(
                    "sandbox database files must be single-link regular files owned by the service user"
                )

    try:
        store = SandboxJobStore(database_path)
        audit_log = RecoveryAuditLog(audit_path)
        os.chmod(audit_path, 0o600)
        runtime = DockerJobRuntime(
            workspace=workspace,
            image=image,
            command=command,
            docker_binary=docker_binary,
        )
        def audit_recovery(event: dict) -> None:
            if not isinstance(event, dict) or event.get("action") != "sandbox.job.recovery":
                raise SandboxJobCompositionError("unexpected sandbox recovery audit event")
            job_id = event.get("job_id")
            previous_state = event.get("previous_state")
            outcome = event.get("outcome")
            if (not isinstance(job_id, str) or not re.fullmatch(r"job_[0-9a-f]{32}", job_id)
                    or previous_state not in {"launching", "running"}
                    or outcome not in {"missing", "removed"}):
                raise SandboxJobCompositionError("malformed sandbox recovery audit event")
            # RecoveryAuditLog has a fixed schema; bind job identity, prior
            # state, and reconciliation result into its bounded action field.
            audit_log.record(
                f"sandbox.recovery.{job_id}.{previous_state}.{outcome}",
                "completed",
            )

        return SandboxJobRecovery(
            store=store,
            runtime=runtime,
            jobs_root=root,
            audit=audit_recovery,
        )
    except Exception as exc:
        if isinstance(exc, SandboxJobCompositionError):
            raise
        raise SandboxJobCompositionError("durable sandbox recovery could not be composed safely") from exc
