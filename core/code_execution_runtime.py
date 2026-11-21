"""Trusted host composition for code-execution grants and durable audit.

This module wires existing Yuki identity policy, OS key storage, and the
append-only approval audit log into the provider-neutral execution service.
It deliberately does not select or enable a sandbox backend.
"""
from __future__ import annotations

from dataclasses import dataclass
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


def derive_execution_grant_key(master_key: bytes) -> bytes:
    """Derive a purpose-separated HMAC key without reusing the vault key bytes."""
    if not isinstance(master_key, bytes) or len(master_key) < 32:
        raise ValueError("OS key provider must return at least 32 bytes")
    return hmac.new(
        master_key,
        b"Yuki execution grant signing key v1",
        hashlib.sha256,
    ).digest()


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
