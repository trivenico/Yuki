"""Trusted host composition for code-execution grants and durable audit.

This module wires existing Yuki identity policy, OS key storage, and the
append-only approval audit log into the provider-neutral execution service.
It deliberately does not select or enable a sandbox backend.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import hmac
from typing import Callable, Protocol

from core.approval_audit import SQLiteApprovalAuditLog
from core.code_execution import CodeExecutionRequest, CodeExecutor
from core.code_execution_service import CodeExecutionService
from core.execution_grants import HMACExecutionGrantAuthority


class ExecutionGrantKeyProvider(Protocol):
    """Trusted OS-backed provider exposing a stable master HMAC secret."""

    def hmac_key(self) -> bytes: ...


@dataclass(frozen=True, slots=True)
class CodeExecutionRuntime:
    """Explicit composition root; grant authority remains host-side."""

    service: CodeExecutionService
    grant_authority: HMACExecutionGrantAuthority


class CodeExecutionAuditRecorder:
    """Adapt execution lifecycle events to Yuki's append-only metadata audit."""

    _EVENTS = {
        "code_execution.started": "execution_started",
        "code_execution.tool_requested": "requested",
        "code_execution.tool_completed": "executed",
        "code_execution.completed": "executed",
        "code_execution.failed": "execution_failed",
    }

    def __init__(self, audit_log: SQLiteApprovalAuditLog) -> None:
        if not isinstance(audit_log, SQLiteApprovalAuditLog):
            raise TypeError("trusted SQLiteApprovalAuditLog is required")
        self._audit_log = audit_log

    def __call__(self, event: str, record: dict[str, object]) -> None:
        event_type = self._EVENTS.get(event)
        if event_type is None:
            raise ValueError("unsupported code execution audit event")
        if not isinstance(record, dict):
            raise ValueError("execution audit record must be a mapping")
        actor_id = record.get("actor_id")
        execution_id = record.get("execution_id")
        if not isinstance(actor_id, str) or not actor_id:
            raise ValueError("execution audit actor is missing")
        if not isinstance(execution_id, str) or not execution_id:
            raise ValueError("execution audit correlation ID is missing")
        # The existing audit schema stores a bounded skill identifier, not
        # source, output, tool arguments, grant tokens, or arbitrary metadata.
        self._audit_log.append(
            event_type=event_type,
            pending_id=hashlib.sha256(execution_id.encode("utf-8")).hexdigest(),
            actor=actor_id,
            skill_id="code-execution",
        )



class TaskRuntimeExecutionPolicy:
    """Bind grants to a live, approved task lease in Yuki's workflow runtime.

    The worker ID is injected by the trusted host composition, never accepted
    from generated code or the execution request. TaskRuntime.get() enforces
    the caller's task:read authorization before the durable record is checked.
    """

    def __init__(self, task_runtime, *, worker_id: str) -> None:
        from automation.task_runtime import TaskRuntime

        if not isinstance(task_runtime, TaskRuntime):
            raise TypeError("trusted Yuki TaskRuntime is required")
        if not isinstance(worker_id, str) or not worker_id.strip() or len(worker_id) > 200 or "\x00" in worker_id:
            raise ValueError("worker_id must be non-empty bounded text")
        self._task_runtime = task_runtime
        self._worker_id = worker_id.strip()

    def __call__(self, request: CodeExecutionRequest) -> bool:
        try:
            visible = self._task_runtime.get(
                actor_id=request.actor_id, task_id=request.task_id
            )
            run = self._task_runtime.store.get_for_owner(
                request.task_id, owner_id=request.actor_id
            )
            if visible.task_id != request.task_id or run is None:
                return False
            if (
                run.state != "running"
                or run.approved_by != request.actor_id
                or not run.approved_at
                or not run.approval_digest
                or run.approval_digest != run.request_digest
            ):
                return False
            expected_session_hash = hashlib.sha256(
                request.session_id.encode("utf-8")
            ).hexdigest()
            if run.approved_session_hash != expected_session_hash:
                return False
            if run.lease_owner != self._worker_id or not run.lease_expires_at:
                return False
            expires_at = datetime.fromisoformat(run.lease_expires_at)
            return expires_at.tzinfo is not None and expires_at > datetime.now(timezone.utc)
        except Exception:
            return False

    def authorize_operation(self, request: CodeExecutionRequest, operation):
        """Serialize the host side effect against task cancellation and lease loss."""
        return self._task_runtime.execute_if_current_lease(
            actor_id=request.actor_id,
            session_id=request.session_id,
            task_id=request.task_id,
            worker_id=self._worker_id,
            operation=operation,
        )


def derive_execution_grant_key(master_key: bytes) -> bytes:
    """Derive a purpose-separated HMAC key without reusing the vault key bytes."""
    if not isinstance(master_key, bytes) or len(master_key) < 32:
        raise ValueError("OS key provider must return at least 32 bytes")
    return hmac.new(
        master_key,
        b"Yuki execution grant signing key v1",
        hashlib.sha256,
    ).digest()



def build_task_bound_code_execution_runtime(
    executor: CodeExecutor,
    *,
    database_path: str,
    task_runtime,
    worker_id: str,
    audit_log: SQLiteApprovalAuditLog,
    key_provider: ExecutionGrantKeyProvider | None = None,
) -> CodeExecutionRuntime:
    """Compose execution with the real task approval/session/lease policy.

    Prefer this entry point for application wiring. It does not accept an
    arbitrary authorization callback that could accidentally trust request
    fields without consulting Yuki's durable workflow state.
    """
    policy = TaskRuntimeExecutionPolicy(task_runtime, worker_id=worker_id)
    return build_code_execution_runtime(
        executor,
        database_path=database_path,
        authorize_request=policy,
        audit_log=audit_log,
        key_provider=key_provider,
    )


def build_code_execution_runtime(
    executor: CodeExecutor,
    *,
    database_path: str,
    authorize_request: Callable[[CodeExecutionRequest], bool],
    audit_log: SQLiteApprovalAuditLog,
    key_provider: ExecutionGrantKeyProvider | None = None,
) -> CodeExecutionRuntime:
    """Compose the trusted control plane; key bootstrap is never implicit.

    The authorization callback must resolve the actor/session/task against
    Yuki's live authenticated application context and current approval policy.
    A callback that merely trusts request fields is not an acceptable policy.
    """
    if not callable(authorize_request):
        raise TypeError("live Yuki request authorization callback is required")
    if not isinstance(audit_log, SQLiteApprovalAuditLog):
        raise TypeError("trusted SQLiteApprovalAuditLog is required")
    if audit_log.verify_chain() is not True:
        raise RuntimeError("Yuki approval audit chain verification failed")
    if key_provider is None:
        from core.oauth_os_key_provider import OSKeyringOAuthKeyProvider
        key_provider = OSKeyringOAuthKeyProvider(service_name="yuki")
    master_key = key_provider.hmac_key()
    signing_key = derive_execution_grant_key(master_key)
    authority = HMACExecutionGrantAuthority(
        database_path,
        key_id="os-keyring-v1",
        signing_key=signing_key,
        authorize_request=authorize_request,
    )
    service = CodeExecutionService(
        executor,
        authority,
        audit=CodeExecutionAuditRecorder(audit_log),
    )
    return CodeExecutionRuntime(service=service, grant_authority=authority)
