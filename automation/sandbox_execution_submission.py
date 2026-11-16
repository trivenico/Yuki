"""Authenticated, owner-scoped facade for durable sandbox execution.

This service is intentionally transport-neutral: HTTP handlers must authenticate
at the edge and call these methods; the raw worker is never a public API.
"""
from __future__ import annotations

from contextlib import closing
from typing import Mapping

from automation.execution_task import (
    DurableSandboxExecutionWorker,
    ExecutionApproval,
    SandboxExecutionTask,
)
from automation.sandbox_execution_approval import SQLiteSandboxExecutionApprovalStore
from core.auth import BuiltInAuthService, AuthenticatedUser
from core.durable_task_engine import DurableTaskEngine, RateLimitExceeded


class SandboxExecutionSubmissionService:
    """Enforce live-session identity and durable goal ownership around a worker."""

    PERMISSION = "workflow:approve"

    def __init__(
        self,
        *,
        auth_service: BuiltInAuthService,
        engine: DurableTaskEngine,
        worker: DurableSandboxExecutionWorker,
        approval_store: SQLiteSandboxExecutionApprovalStore,
    ) -> None:
        if not isinstance(auth_service, BuiltInAuthService):
            raise TypeError("auth_service must be a BuiltInAuthService")
        if not isinstance(engine, DurableTaskEngine):
            raise TypeError("engine must be a DurableTaskEngine")
        if not isinstance(worker, DurableSandboxExecutionWorker) or worker.engine is not engine:
            raise TypeError("worker must use the supplied task engine")
        if not isinstance(approval_store, SQLiteSandboxExecutionApprovalStore):
            raise TypeError("approval_store must be a SQLiteSandboxExecutionApprovalStore")
        if approval_store.auth_service is not auth_service:
            raise ValueError("approval store must use the same authentication service")
        self.auth_service = auth_service
        self.engine = engine
        self.worker = worker
        self.approval_store = approval_store
        # Ownership is persisted transactionally with each goal by the engine.

    def _principal(self, session_token: str, *, permission: str | None = None) -> AuthenticatedUser:
        user = self.auth_service.resolve_session(session_token)
        if user is None:
            raise PermissionError("A live authenticated session is required.")
        if permission is not None and permission not in user.permissions:
            raise PermissionError("The authenticated principal lacks the required permission.")
        return user

    def _owns_goal(self, goal_id: str, actor_id: str) -> bool:
        return self.engine.goal_owner(goal_id) == actor_id

    def create_goal(self, *, session_token: str) -> str:
        user = self._principal(session_token, permission="workflow:read")
        if not self.engine.consume_rate_limit(user.user_id, "sandbox.goal.create", limit=10, window_seconds=60):
            raise RateLimitExceeded("goal creation rate limit exceeded")
        return self.engine.create_owned_goal(user.user_id)

    def approve_task(
        self, task: SandboxExecutionTask, *, session_token: str,
    ) -> ExecutionApproval:
        user = self._principal(session_token, permission=self.PERMISSION)
        if not self.engine.consume_rate_limit(user.user_id, "sandbox.approval.create", limit=20, window_seconds=3600):
            raise RateLimitExceeded("approval creation rate limit exceeded")
        return self.approval_store.approve(task, session_token=session_token)

    def submit(
        self,
        goal_id: str,
        task: SandboxExecutionTask,
        approval: ExecutionApproval,
        *,
        idempotency_key: str,
        session_token: str,
    ) -> Mapping[str, object]:
        user = self._principal(session_token, permission=self.PERMISSION)
        if not self._owns_goal(goal_id, user.user_id):
            # Do not reveal whether another user's goal exists.
            raise PermissionError("Goal is unavailable.")
        if not isinstance(approval, ExecutionApproval) or approval.actor_id != user.user_id:
            raise PermissionError("The approval belongs to a different principal.")
        # Exact retries do not consume another rate-limit slot.
        prior = self.engine.get_submission_receipt(
            goal_id, idempotency_key, actor_id=approval.actor_id,
            approval_id=approval.approval_id,
            task_fingerprint=approval.task_fingerprint,
        )
        if prior is not None:
            return prior
        return self.worker.submit(
            goal_id, task, approval, idempotency_key=idempotency_key,
            rate_limit=(user.user_id, "sandbox.task.submit", 10, 60),
        )

    def get_task(self, task_id: str, *, session_token: str) -> Mapping[str, object]:
        user = self._principal(session_token, permission="workflow:read")
        task = self.engine.get_task(task_id)
        if not self._owns_goal(task["goal_id"], user.user_id):
            raise PermissionError("Task is unavailable.")
        return task

    def list_tasks(self, goal_id: str, *, session_token: str) -> list[Mapping[str, object]]:
        user = self._principal(session_token, permission="workflow:read")
        if not self._owns_goal(goal_id, user.user_id):
            raise PermissionError("Goal is unavailable.")
        return self.engine.list_tasks(goal_id)

    def cancel_task(self, task_id: str, *, session_token: str) -> Mapping[str, object]:
        user = self._principal(session_token, permission="workflow:approve")
        task = self.engine.get_task(task_id)
        if not self._owns_goal(task["goal_id"], user.user_id):
            raise PermissionError("Task is unavailable.")
        return self.worker.cancel(task_id)
