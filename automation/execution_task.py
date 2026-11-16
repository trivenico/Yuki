"""Explicit approval-bound contract for isolated code execution tasks.

This module does not grant execution authority to ordinary specialist tasks.
A trusted approval verifier and the configured DockerSandbox are mandatory.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from typing import Callable, Mapping, Protocol

from hackback.sandbox import DockerSandbox

_TASK_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_ACTOR_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.@:-]{0,199}$")
_APPROVAL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")


@dataclass(frozen=True)
class SandboxExecutionTask:
    """A bounded request for one pinned-image command in one workspace."""

    task_id: str
    image: str
    argv: tuple[str, ...]
    workspace: str
    timeout_seconds: int = 30
    write_workspace: bool = False
    max_output_bytes: int = 24_000

    def validate(self) -> "SandboxExecutionTask":
        if not isinstance(self.task_id, str) or not _TASK_ID.fullmatch(self.task_id):
            raise ValueError("task_id must be a bounded identifier")
        if not isinstance(self.image, str) or "@sha256:" not in self.image or len(self.image) > 255:
            raise ValueError("execution image must be a bounded digest-pinned reference")
        if not isinstance(self.argv, tuple) or not 1 <= len(self.argv) <= 64:
            raise ValueError("argv must be a tuple containing 1 to 64 arguments")
        if any(not isinstance(arg, str) or not arg or len(arg) > 500 or "\x00" in arg for arg in self.argv):
            raise ValueError("argv arguments must be bounded non-empty NUL-free strings")
        if not isinstance(self.workspace, str) or not self.workspace or len(self.workspace) > 4096 or "\x00" in self.workspace:
            raise ValueError("workspace must be a bounded path")
        if type(self.timeout_seconds) is not int or not 1 <= self.timeout_seconds <= 120:
            raise ValueError("timeout_seconds must be between 1 and 120")
        if type(self.write_workspace) is not bool:
            raise ValueError("write_workspace must be a boolean")
        if type(self.max_output_bytes) is not int or not 1 <= self.max_output_bytes <= 1_048_576:
            raise ValueError("max_output_bytes must be between 1 and 1048576")
        return self

    def fingerprint(self) -> str:
        """Stable digest binding approval to every security-relevant task field."""
        self.validate()
        payload = {
            "task_id": self.task_id,
            "image": self.image,
            "argv": list(self.argv),
            "workspace": self.workspace,
            "timeout_seconds": self.timeout_seconds,
            "write_workspace": self.write_workspace,
            "max_output_bytes": self.max_output_bytes,
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class ExecutionApproval:
    """Approval provenance supplied by the durable authorization subsystem."""

    approval_id: str
    actor_id: str
    task_id: str
    task_fingerprint: str
    approved: bool


class SandboxRunner(Protocol):
    workspace: object

    def run(
        self, image: str, argv: tuple[str, ...], *, timeout: int,
        write_workspace: bool, max_output_bytes: int,
    ) -> Mapping[str, object]: ...


ApprovalVerifier = Callable[[ExecutionApproval, SandboxExecutionTask], bool]
AuditWriter = Callable[[Mapping[str, object]], object]


def execute_approved_task(
    task: SandboxExecutionTask,
    approval: ExecutionApproval,
    *,
    sandbox: SandboxRunner,
    verify_approval: ApprovalVerifier,
    audit: AuditWriter,
) -> Mapping[str, object]:
    """Verify exact-task approval, audit, then invoke the guarded sandbox once.

    The verifier must consult the trusted approval store and verify approver
    authority/revocation. A boolean supplied by a model or task payload is not
    sufficient. Audit-start failure prevents execution.
    """
    task.validate()
    if not isinstance(approval, ExecutionApproval):
        raise TypeError("approval must be an ExecutionApproval")
    if not _APPROVAL_ID.fullmatch(approval.approval_id or ""):
        raise ValueError("approval_id must be a bounded identifier")
    if not _ACTOR_ID.fullmatch(approval.actor_id or ""):
        raise ValueError("actor_id must be a bounded identifier")
    if approval.task_id != task.task_id or approval.task_fingerprint != task.fingerprint():
        raise PermissionError("approval does not bind to this exact execution task")
    if approval.approved is not True or not callable(verify_approval):
        raise PermissionError("a trusted approval verifier is required")
    if verify_approval(approval, task) is not True:
        raise PermissionError("execution approval was not verified")
    if not isinstance(sandbox, DockerSandbox):
        raise TypeError("sandbox must be a configured DockerSandbox")
    if str(sandbox.workspace) != str(__import__("pathlib").Path(task.workspace).expanduser().resolve()):
        raise PermissionError("task workspace does not match the configured sandbox workspace")
    if not callable(audit):
        raise TypeError("a durable audit writer is required")

    audit_base = {
        "task_id": task.task_id,
        "approval_id": approval.approval_id,
        "actor_id": approval.actor_id,
        "task_fingerprint": task.fingerprint(),
    }
    audit({**audit_base, "event": "execution.started"})
    try:
        result = sandbox.run(
            task.image,
            task.argv,
            timeout=task.timeout_seconds,
            write_workspace=task.write_workspace,
            max_output_bytes=task.max_output_bytes,
        )
    except Exception:
        audit({**audit_base, "event": "execution.failed"})
        raise
    audit({**audit_base, "event": "execution.completed"})
    if not isinstance(result, Mapping):
        raise RuntimeError("sandbox returned an invalid execution result")
    return result
