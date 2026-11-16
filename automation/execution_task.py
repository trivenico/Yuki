"""Explicit approval-bound contract for isolated code execution tasks.

This module does not grant execution authority to ordinary specialist tasks.
A trusted approval verifier and the configured DockerSandbox are mandatory.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from pathlib import Path
from typing import Callable, Mapping

from core.durable_task_engine import DurableTaskEngine
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
        if not isinstance(self.workspace, str) or not self.workspace or len(self.workspace) > 4096 or "\x00" in self.workspace or not Path(self.workspace).expanduser().is_absolute():
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


ApprovalVerifier = Callable[[ExecutionApproval, SandboxExecutionTask], bool]
AuditWriter = Callable[[Mapping[str, object]], object]


class ExecutionOutcomeUnknown(RuntimeError):
    """A sandbox side effect may have occurred but durable outcome recording failed."""


def execute_approved_task(
    task: SandboxExecutionTask,
    approval: ExecutionApproval,
    *,
    sandbox: DockerSandbox,
    verify_approval: ApprovalVerifier,
    audit: AuditWriter,
    consume_approval: Callable[[ExecutionApproval, SandboxExecutionTask], bool] | None = None,
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
    if str(sandbox.workspace) != str(Path(task.workspace).expanduser().resolve()):
        raise PermissionError("task workspace does not match the configured sandbox workspace")
    if not callable(audit):
        raise TypeError("a durable audit writer is required")
    if consume_approval is not None:
        if not callable(consume_approval) or consume_approval(approval, task) is not True:
            raise PermissionError("execution approval could not be atomically consumed")

    audit_base = {
        "task_id": task.task_id,
        "approval_id": approval.approval_id,
        "actor_id": approval.actor_id,
        "task_fingerprint": task.fingerprint(),
    }
    if audit({**audit_base, "event": "execution.started"}) is False:
        raise RuntimeError("execution start audit was not durably accepted")
    try:
        result = sandbox.run(
            task.image,
            task.argv,
            timeout=task.timeout_seconds,
            write_workspace=task.write_workspace,
            max_output_bytes=task.max_output_bytes,
        )
    except Exception as execution_error:
        # Docker timeout/termination errors do not prove that the process had
        # no side effects; conservatively persist an unknown outcome.
        try:
            accepted = audit({**audit_base, "event": "execution.unknown"})
        except Exception as audit_error:
            raise ExecutionOutcomeUnknown(
                "sandbox may have had side effects and its outcome audit could not be persisted"
            ) from audit_error
        if accepted is False:
            raise ExecutionOutcomeUnknown(
                "sandbox may have had side effects but its outcome audit was rejected"
            ) from execution_error
        raise ExecutionOutcomeUnknown(
            "sandbox dispatch failed; side-effect outcome is not proven"
        ) from execution_error
    if not isinstance(result, Mapping):
        try:
            audit({**audit_base, "event": "execution.unknown"})
        except Exception:
            pass
        raise ExecutionOutcomeUnknown("sandbox returned an invalid result after dispatch")
    try:
        accepted = audit({**audit_base, "event": "execution.completed"})
    except Exception as audit_error:
        try:
            audit({**audit_base, "event": "execution.unknown"})
        except Exception:
            pass
        raise ExecutionOutcomeUnknown(
            "sandbox may have completed but its completion audit could not be persisted"
        ) from audit_error
    if accepted is False:
        try:
            audit({**audit_base, "event": "execution.unknown"})
        except Exception:
            pass
        raise ExecutionOutcomeUnknown(
            "sandbox may have completed but its completion audit was rejected"
        )
    return result


EXECUTION_TASK_TYPE = "sandbox.execution.v1"


class DurableSandboxExecutionWorker:
    """Dedicated lease-owning worker for approved sandbox tasks only.

    It never claims specialist text tasks. Execution tasks are submitted with
    one attempt so lease recovery cannot silently replay a possibly side-effecting
    command. A recovered task requires a fresh, explicit submission/approval.
    """

    def __init__(
        self,
        engine: DurableTaskEngine,
        *,
        worker_id: str,
        sandbox: DockerSandbox,
        verify_approval: ApprovalVerifier,
        audit: AuditWriter,
        consume_approval: Callable[[ExecutionApproval, SandboxExecutionTask], bool] | None = None,
    ) -> None:
        if not isinstance(engine, DurableTaskEngine):
            raise TypeError("engine must be a DurableTaskEngine")
        if not isinstance(worker_id, str) or not worker_id.strip() or len(worker_id) > 256:
            raise ValueError("worker_id must be bounded non-empty text")
        if not isinstance(sandbox, DockerSandbox):
            raise TypeError("sandbox must be a configured DockerSandbox")
        if not callable(verify_approval) or not callable(audit):
            raise TypeError("trusted approval verifier and durable audit writer are required")
        self.engine = engine
        self.worker_id = worker_id.strip()
        self.sandbox = sandbox
        self.verify_approval = verify_approval
        self.audit = audit
        self.consume_approval = consume_approval

    @classmethod
    def from_trusted_stores(
        cls, engine: DurableTaskEngine, *, worker_id: str,
        sandbox: DockerSandbox, approval_store, audit_writer,
    ) -> "DurableSandboxExecutionWorker":
        """Compose the worker with Yuki's concrete persisted security stores."""
        from automation.sandbox_execution_approval import (
            SQLiteSandboxExecutionApprovalStore, SandboxExecutionAuditWriter,
        )
        if not isinstance(approval_store, SQLiteSandboxExecutionApprovalStore):
            raise TypeError("approval_store must be a SQLiteSandboxExecutionApprovalStore")
        if not isinstance(audit_writer, SandboxExecutionAuditWriter):
            raise TypeError("audit_writer must be a SandboxExecutionAuditWriter")
        return cls(
            engine, worker_id=worker_id, sandbox=sandbox,
            verify_approval=approval_store.verify, audit=audit_writer,
            consume_approval=approval_store.consume,
        )

    def submit(
        self,
        goal_id: str,
        task: SandboxExecutionTask,
        approval: ExecutionApproval,
        *,
        idempotency_key: str,
    ) -> Mapping[str, object]:
        task.validate()
        if not isinstance(approval, ExecutionApproval) or approval.approved is not True:
            raise PermissionError("an approved execution record is required")
        if approval.task_id != task.task_id or approval.task_fingerprint != task.fingerprint():
            raise PermissionError("approval does not bind to this exact execution task")
        if not callable(self.verify_approval) or self.verify_approval(approval, task) is not True:
            raise PermissionError("execution approval was not verified at submission")
        if str(self.sandbox.workspace) != str(Path(task.workspace).expanduser().resolve()):
            raise PermissionError("task workspace does not match the configured sandbox workspace")
        payload = {
            "task": {
                "task_id": task.task_id,
                "image": task.image,
                "argv": list(task.argv),
                "workspace": task.workspace,
                "timeout_seconds": task.timeout_seconds,
                "write_workspace": task.write_workspace,
                "max_output_bytes": task.max_output_bytes,
            },
            "approval": {
                "approval_id": approval.approval_id,
                "actor_id": approval.actor_id,
                "task_id": approval.task_id,
                "task_fingerprint": approval.task_fingerprint,
                "approved": approval.approved,
            },
        }
        return self.engine.submit(
            goal_id, EXECUTION_TASK_TYPE, payload,
            idempotency_key=idempotency_key, max_attempts=1,
        )

    def run_once(self, *, lease_seconds: int = 180) -> Mapping[str, object] | None:
        claimed = self.engine.claim(
            self.worker_id, lease_seconds=lease_seconds,
            task_types=(EXECUTION_TASK_TYPE,),
        )
        if claimed is None:
            return None
        try:
            payload = claimed["payload"]
            if not isinstance(payload, dict) or set(payload) != {"task", "approval"}:
                raise ValueError("invalid execution task payload")
            raw_task, raw_approval = payload["task"], payload["approval"]
            if not isinstance(raw_task, dict) or not isinstance(raw_approval, dict):
                raise ValueError("invalid execution task payload")
            task = SandboxExecutionTask(
                task_id=raw_task["task_id"], image=raw_task["image"],
                argv=tuple(raw_task["argv"]), workspace=raw_task["workspace"],
                timeout_seconds=raw_task["timeout_seconds"],
                write_workspace=raw_task["write_workspace"],
                max_output_bytes=raw_task["max_output_bytes"],
            )
            approval = ExecutionApproval(**raw_approval)
            result = execute_approved_task(
                task, approval, sandbox=self.sandbox,
                verify_approval=self.verify_approval, audit=self.audit,
                consume_approval=self.consume_approval,
            )
            try:
                return self.engine.complete(
                    claimed["id"], self.worker_id, dict(result)
                )
            except Exception as completion_error:
                # A database/connection error can occur after COMMIT reached
                # SQLite. Read back before classifying the durable result.
                try:
                    current = self.engine.get_task(claimed["id"])
                except Exception:
                    current = None
                if current is not None and current.get("state") == "succeeded":
                    return current
                try:
                    self.engine.fail_unknown_outcome(
                        claimed["id"], self.worker_id
                    )
                except Exception:
                    pass
                raise ExecutionOutcomeUnknown(
                    "sandbox completed but its durable task result could not be confirmed"
                ) from completion_error
        except ExecutionOutcomeUnknown:
            self.engine.fail_unknown_outcome(claimed["id"], self.worker_id)
            raise
        except Exception:
            self.engine.fail(
                claimed["id"], self.worker_id, "sandbox execution failed",
                retry_delay_seconds=0,
            )
            raise

    def cancel(self, task_id: str) -> Mapping[str, object]:
        """Cancel this worker's queued execution tasks, never another task class."""
        current = self.engine.get_task(task_id)
        if current["task_type"] != EXECUTION_TASK_TYPE:
            raise PermissionError("worker cannot cancel a different task type")
        return self.engine.cancel_task(task_id)
