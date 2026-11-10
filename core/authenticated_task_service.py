"""Authenticated facade for durable task ownership and access control.

Callers provide an opaque session token obtained from BuiltInAuthService.
The principal is always resolved server-side; owner identity is never accepted
from task payloads or caller-supplied owner_id arguments.
"""
from __future__ import annotations

from typing import Any

from core.auth import AuthenticationError, BuiltInAuthService
from core.durable_task_engine import DurableTaskEngine, TaskNotFound


class AuthenticatedDurableTaskService:
    def __init__(self, auth: BuiltInAuthService, engine: DurableTaskEngine):
        if not isinstance(auth, BuiltInAuthService):
            raise TypeError("auth must be a BuiltInAuthService")
        if not isinstance(engine, DurableTaskEngine):
            raise TypeError("engine must be a DurableTaskEngine")
        self.auth = auth
        self.engine = engine

    def _principal(self, session_token: str):
        user = self.auth.resolve_session(session_token)
        if user is None:
            raise AuthenticationError("Authentication required.")
        return user

    def _owned_goal(self, user_id: str, goal_id: str) -> dict[str, Any]:
        goal = self.engine.get_goal(goal_id)
        if goal.get("owner_id") != user_id:
            # Do not reveal whether another user's goal exists.
            raise TaskNotFound("goal not found")
        return goal

    def create_goal(self, session_token: str) -> str:
        user = self._principal(session_token)
        return self.engine.create_goal(owner_id=user.user_id)

    def submit(
        self,
        session_token: str,
        goal_id: str,
        task_type: str,
        payload: Any,
        *,
        idempotency_key: str,
        dependencies=(),
        max_attempts: int = 3,
        delay_seconds: float = 0,
    ) -> dict[str, Any]:
        user = self._principal(session_token)
        self._owned_goal(user.user_id, goal_id)
        return self.engine.submit(
            goal_id,
            task_type,
            payload,
            idempotency_key=idempotency_key,
            owner_id=user.user_id,
            dependencies=dependencies,
            max_attempts=max_attempts,
            delay_seconds=delay_seconds,
        )

    def get_goal(self, session_token: str, goal_id: str) -> dict[str, Any]:
        user = self._principal(session_token)
        return self._owned_goal(user.user_id, goal_id)

    def get_task(self, session_token: str, task_id: str) -> dict[str, Any]:
        user = self._principal(session_token)
        task = self.engine.get_task(task_id)
        if task.get("owner_id") != user.user_id:
            raise TaskNotFound("task not found")
        return task

    def list_tasks(self, session_token: str, goal_id: str) -> list[dict[str, Any]]:
        user = self._principal(session_token)
        self._owned_goal(user.user_id, goal_id)
        return self.engine.list_tasks(goal_id)

    def list_events(
        self, session_token: str, goal_id: str, *, after_id: int = 0, limit: int = 100
    ) -> list[dict[str, Any]]:
        user = self._principal(session_token)
        self._owned_goal(user.user_id, goal_id)
        return self.engine.list_events(goal_id, after_id=after_id, limit=limit)

    def cancel_task(self, session_token: str, task_id: str) -> dict[str, Any]:
        user = self._principal(session_token)
        task = self.engine.get_task(task_id)
        if task.get("owner_id") != user.user_id:
            raise TaskNotFound("task not found")
        return self.engine.cancel_task(task_id)
