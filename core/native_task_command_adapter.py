"""Narrow native command adapters for Yuki's durable task runtime.

The adapter exposes read-only task commands only. TaskRuntime remains the
resource-level authorization boundary; the supplied capability check is an
additional application-level gate used by NativeIntentDispatcher.
"""
from __future__ import annotations

import json
from typing import Any, Callable, Mapping

from automation.task_runtime import TaskRuntime
from core.native_intents import IntentKind


class NativeTaskCommandAdapter:
    """Expose task list/show as bounded native intent handlers."""

    _CAPABILITIES = {
        IntentKind.TASK_LIST: "task:list",
        IntentKind.TASK_SHOW: "task:read",
    }

    def __init__(
        self,
        runtime: TaskRuntime,
        *,
        authorize_capability: Callable[[str, str], bool],
        list_limit: int = 20,
    ) -> None:
        if not isinstance(runtime, TaskRuntime):
            raise TypeError("runtime must be a TaskRuntime.")
        if not callable(authorize_capability):
            raise TypeError("authorize_capability must be callable.")
        if type(list_limit) is not int or not 1 <= list_limit <= 50:
            raise ValueError("list_limit must be an integer from 1 to 50.")
        self._runtime = runtime
        self._authorize_capability = authorize_capability
        self._list_limit = list_limit

    @property
    def handlers(self) -> Mapping[IntentKind, Callable[[str, tuple[str, ...]], str]]:
        return {
            IntentKind.TASK_LIST: self._list_tasks,
            IntentKind.TASK_SHOW: self._show_task,
        }

    def authorize_intent(self, actor_id: str, kind: IntentKind) -> bool:
        """Check the coarse app capability; TaskRuntime checks task-level scope."""
        capability = self._CAPABILITIES.get(kind)
        if capability is None:
            return False
        try:
            return self._authorize_capability(actor_id, capability) is True
        except Exception:
            return False

    def _list_tasks(self, actor_id: str, arguments: tuple[str, ...]) -> str:
        if arguments:
            raise ValueError("task list does not accept arguments.")
        # Use redacted summaries, never the full task definitions or results.
        summaries = self._runtime.list_task_summaries(
            actor_id=actor_id, limit=self._list_limit,
        )
        payload = [
            {
                "task_id": item.task_id,
                "name": item.name,
                "state": item.state,
                "version": item.version,
                "attempts": item.attempts,
                "created_at": item.created_at,
                "updated_at": item.updated_at,
            }
            for item in summaries
        ]
        return json.dumps({"tasks": payload}, ensure_ascii=False, separators=(",", ":"))

    def _show_task(self, actor_id: str, arguments: tuple[str, ...]) -> str:
        if len(arguments) != 1:
            raise ValueError("task show requires exactly one task ID.")
        task = self._runtime.get(actor_id=actor_id, task_id=arguments[0])
        # Deliberately omit steps, result payloads, lease-owner details and event
        # contents from the conversational surface. Those remain in task APIs.
        payload: dict[str, Any] = {
            "task_id": task.task_id,
            "name": task.name,
            "state": task.state,
            "step_count": len(task.steps),
            "error_code": task.error_code,
            "version": task.version,
            "attempts": task.attempts,
            "created_at": task.created_at,
            "updated_at": task.updated_at,
        }
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


__all__ = ["NativeTaskCommandAdapter"]
