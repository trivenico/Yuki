"""Durable, claim-bound worker for owner-approved workspace operations.

This worker is intentionally separate from DurableSkillWorker: it accepts one
fixed workspace.apply task contract and has no generic tool, shell, or skill
dispatch path. All writes flow through the typed approval gateway and the
claim-fenced workspace executor.
"""
from __future__ import annotations

import hashlib
import math
import threading
import time
from typing import Any, Callable

from automation.approved_workspace_changes import FileChange
from automation.authorized_read_tools import ToolPrincipal
from core.approved_workspace_operation_executor import apply_claim_bound_workspace_changes
from core.guarded_skill_runtime import SkillPolicyContext
from core.workspace_approval_operation import build_workspace_approval_operation


class DurableWorkspaceWorker:
    TASK_TYPE = "workspace.apply"

    def __init__(
        self,
        engine: Any,
        *,
        gateway: Any,
        executor: Any,
        worker_id: str,
        policy_request_factory: Callable[..., Any],
        principal_factory: Callable[[SkillPolicyContext], ToolPrincipal],
        approval_token_resolver: Callable[[SkillPolicyContext, str, str], str | None],
        lease_seconds: int = 60,
        heartbeat_interval_seconds: float = 20,
        approval_wait_timeout_seconds: int = 86400,
    ) -> None:
        if not isinstance(worker_id, str) or not worker_id or len(worker_id) > 256:
            raise ValueError("worker_id must be non-empty bounded text")
        if not 2 <= lease_seconds <= 3600:
            raise ValueError("lease_seconds must be between 2 and 3600")
        if (isinstance(heartbeat_interval_seconds, bool)
                or not isinstance(heartbeat_interval_seconds, (int, float))
                or not math.isfinite(heartbeat_interval_seconds)
                or not 0 < heartbeat_interval_seconds <= min(lease_seconds / 2, 300)):
            raise ValueError("heartbeat interval must be positive and at most half the lease")
        if (isinstance(approval_wait_timeout_seconds, bool)
                or not isinstance(approval_wait_timeout_seconds, int)
                or not 1 <= approval_wait_timeout_seconds <= 86400):
            raise ValueError("approval wait timeout must be between 1 and 86400 seconds")
        for name, callback in (
            ("policy_request_factory", policy_request_factory),
            ("principal_factory", principal_factory),
            ("approval_token_resolver", approval_token_resolver),
        ):
            if not callable(callback):
                raise TypeError(f"{name} must be callable")
        resolver_worker_id = getattr(approval_token_resolver, "worker_id", None)
        if resolver_worker_id is not None and resolver_worker_id != worker_id:
            raise ValueError("approval resolver is bound to a different worker")
        self.engine, self.gateway, self.executor = engine, gateway, executor
        self.worker_id = worker_id
        self.policy_request_factory = policy_request_factory
        self.principal_factory = principal_factory
        self.approval_token_resolver = approval_token_resolver
        self.lease_seconds = lease_seconds
        self.heartbeat_interval_seconds = float(heartbeat_interval_seconds)
        self.approval_wait_timeout_seconds = approval_wait_timeout_seconds

    def _reject(self, task_id: str, token: str, reason: str) -> dict[str, Any]:
        result = self.engine.reject(
            task_id, self.worker_id, reason, lease_token=token,
        )
        return {"task_id": task_id, "state": result["state"], "status": reason}

    def run_once(self) -> dict[str, Any]:
        task = self.engine.claim(self.worker_id, lease_seconds=self.lease_seconds)
        if task is None:
            return {"state": "idle", "status": "no_task"}
        task_id, lease_token = task.get("id"), task.get("lease_token")
        if not isinstance(task_id, str) or not isinstance(lease_token, str) or len(lease_token) < 32:
            raise RuntimeError("durable workspace claim is malformed")
        try:
            identity = self.engine.get_claim_identity(task_id, self.worker_id, lease_token)
            if (not isinstance(identity, dict)
                    or set(identity) != {"owner_id", "goal_id", "task_id"}
                    or identity.get("task_id") != task_id
                    or not isinstance(identity.get("owner_id"), str)
                    or not identity["owner_id"]):
                return self._reject(task_id, lease_token, "invalid_claim_identity")
            if task.get("task_type") != self.TASK_TYPE or task.get("max_attempts") != 1:
                return self._reject(task_id, lease_token, "unsupported_or_retryable_workspace_task")
            payload = task.get("payload")
            if not isinstance(payload, dict) or set(payload) != {"workspace_id", "changes"}:
                return self._reject(task_id, lease_token, "invalid_workspace_payload")
            if not isinstance(payload["workspace_id"], str) or not isinstance(payload["changes"], list):
                return self._reject(task_id, lease_token, "invalid_workspace_payload")
            changes = tuple(
                FileChange(
                    path=item["path"],
                    expected_sha256=item["expected_sha256"],
                    content=item["content"],
                )
                for item in payload["changes"]
                if isinstance(item, dict) and set(item) == {"path", "expected_sha256", "content"}
            )
            if len(changes) != len(payload["changes"]):
                return self._reject(task_id, lease_token, "invalid_workspace_payload")
            context = SkillPolicyContext(
                principal_id=identity["owner_id"],
                trigger="durable_task",
                goal_id=identity["goal_id"],
                task_id=task_id,
                claim_token_sha256=hashlib.sha256(lease_token.encode("ascii")).hexdigest(),
            )
            operation, operation_payload = build_workspace_approval_operation(
                workspace_id=payload["workspace_id"], changes=changes,
            )
            principal = self.principal_factory(context)
            if not isinstance(principal, ToolPrincipal) or principal.actor_id != context.principal_id:
                raise PermissionError("workspace principal does not match the durable owner")
            self.executor.validate_principal(principal)
            self.executor.validate_proposal(changes)
            request = self.policy_request_factory(
                context, payload["workspace_id"], changes, operation,
            )
            from core.tool_policy import Decision, PolicyRequest
            if not isinstance(request, PolicyRequest):
                raise TypeError("workspace policy factory must return PolicyRequest")
            if (
                request.principal_id != context.principal_id
                or request.trigger != context.trigger
                or request.run_id != context.goal_id
                or request.step_id != context.task_id
                or request.claim_token_sha256 != context.claim_token_sha256
                or request.capability != operation.capability
                or request.target != operation.target
                or request.argument_sha256 != operation.payload_sha256
            ):
                raise PermissionError("workspace policy request is not bound to this exact claim and operation")
            if self.engine.validate_lease(task_id, self.worker_id, lease_token) is not True:
                raise PermissionError("workspace task claim expired before approval request")
            policy = self.gateway.policy_engine.evaluate(request)
            if policy.decision != Decision.REQUIRE_APPROVAL:
                raise PermissionError("workspace operation requires an explicit owner approval policy")
            request_id = self.gateway.request_approval(
                request, operation=operation, operation_payload=operation_payload,
            )
            self.engine.record_approval_wait(task_id, self.worker_id, lease_token, request_id)
            claim_digest = context.claim_token_sha256
            stop = threading.Event()
            heartbeat_errors: list[Exception] = []

            def heartbeat() -> None:
                while not stop.wait(self.heartbeat_interval_seconds):
                    try:
                        self.engine.heartbeat(
                            task_id, self.worker_id, lease_token=lease_token,
                            lease_seconds=self.lease_seconds,
                        )
                    except Exception as error:
                        heartbeat_errors.append(error)
                        stop.set()
                        return

            thread = threading.Thread(target=heartbeat, name=f"yuki-workspace-{task_id[:8]}", daemon=True)
            thread.start()
            try:
                deadline = time.monotonic() + self.approval_wait_timeout_seconds
                token = None
                while token is None:
                    if self.engine.validate_lease(task_id, self.worker_id, lease_token) is not True:
                        raise PermissionError("workspace task claim changed while awaiting approval")
                    status = self.gateway.get_request_status(request_id)
                    if status in {"rejected", "expired"}:
                        self.engine.transition_approval_wait(task_id, claim_digest, status)
                        raise PermissionError("owner rejected or approval expired")
                    if heartbeat_errors:
                        raise RuntimeError("workspace task lease heartbeat failed")
                    token = self.approval_token_resolver(context, self.TASK_TYPE, lease_token)
                    if token is None:
                        if time.monotonic() >= deadline:
                            raise TimeoutError("owner approval wait expired")
                        stop.wait(min(0.5, max(0.01, deadline - time.monotonic())))
                if not isinstance(token, str) or not 40 <= len(token) <= 256:
                    raise PermissionError("approval resolver returned an invalid credential")
                if self.engine.validate_lease(task_id, self.worker_id, lease_token) is not True:
                    raise PermissionError("workspace task claim changed during approval delivery")
                if not self.engine.transition_approval_wait(task_id, claim_digest, "delivered"):
                    raise PermissionError("durable approval delivery could not be recorded")
                wait = self.engine.get_approval_wait(task_id, claim_digest)
                if wait is None or wait["state"] != "delivered" or wait["request_id"] != request_id:
                    raise PermissionError("approval delivery is not bound to this claim")
                result = apply_claim_bound_workspace_changes(
                    gateway=self.gateway, executor=self.executor, request=request,
                    approval_token=token, principal=principal,
                    workspace_id=payload["workspace_id"], changes=changes,
                    validate_live_claim=lambda: self.engine.validate_lease(
                        task_id, self.worker_id, lease_token,
                    ),
                )
            finally:
                stop.set()
                thread.join(timeout=max(1.0, self.heartbeat_interval_seconds + 0.5))
            if heartbeat_errors:
                raise RuntimeError("workspace lease heartbeat failed") from heartbeat_errors[0]
            completed = self.engine.complete(
                task_id, self.worker_id,
                {"ok": True, "status": "workspace_changes_applied",
                 "changed_paths": list(result.changed_paths)},
                lease_token=lease_token,
            )
            return {"task_id": task_id, "state": completed["state"],
                    "status": "workspace_changes_applied",
                    "changed_paths": list(result.changed_paths)}
        except Exception as exc:
            # Expose only a bounded exception type for operations diagnostics; never persist messages or secrets.
            failure_code = type(exc).__name__
            if not isinstance(failure_code, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9]{0,63}", failure_code):
                failure_code = "ExecutionError"
            cause_code = type(exc.__cause__).__name__ if exc.__cause__ is not None else None
            if not isinstance(cause_code, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9]{0,63}", cause_code or ""):
                cause_code = None
            # Side effects are not replayed: workspace tasks are single-attempt.
            try:
                current = self.engine.get_task(task_id)
                if current is not None and current.get("state") == "running":
                    failed = self.engine.fail(
                        task_id, self.worker_id, "workspace worker execution failed",
                        lease_token=lease_token, retry_delay_seconds=0,
                    )
                    return {"task_id": task_id, "state": failed["state"],
                            "status": "workspace_execution_failed"}
            except Exception:
                pass
            return {"task_id": task_id, "state": "unknown",
                    "status": "workspace_claim_lost_or_outcome_ambiguous"}
