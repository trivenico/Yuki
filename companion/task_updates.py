"""Safe, incremental task updates for Yuki's companion interface.

This adapter exposes only presentation-safe event summaries. It deliberately
omits task arguments, artifact references, model prompts, and result payloads.
The TaskRuntime remains responsible for authorization and event pagination.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

from automation.task_runtime import TaskRuntime

_CONTROL = re.compile(r"[\\x00-\\x1f\\x7f]")


@dataclass(frozen=True)
class CompanionTaskUpdate:
    task_id: str
    sequence: int
    kind: str
    message: str
    percent: int | None = None


class CompanionTaskUpdates:
    """Translate authorized durable task events into bounded companion updates."""

    MAX_PAGE_SIZE = 100
    MAX_MESSAGE_CHARS = 300

    def __init__(self, runtime: TaskRuntime) -> None:
        if not isinstance(runtime, TaskRuntime):
            raise TypeError("runtime must be a TaskRuntime.")
        self.runtime = runtime

    @classmethod
    def _safe_text(cls, value: Any, *, fallback: str) -> str:
        if not isinstance(value, str):
            return fallback
        text = _CONTROL.sub(" ", value).strip()
        return text[:cls.MAX_MESSAGE_CHARS] or fallback

    def updates(
        self, *, actor_id: str, task_id: str, after_sequence: int = 0,
        limit: int = 50,
    ) -> list[CompanionTaskUpdate]:
        """Return a bounded page; authorization is enforced by TaskRuntime.events."""
        if type(after_sequence) is not int or after_sequence < 0:
            raise ValueError("after_sequence must be a non-negative integer.")
        if type(limit) is not int or not 1 <= limit <= self.MAX_PAGE_SIZE:
            raise ValueError("limit must be between 1 and 100.")
        events = self.runtime.events(
            actor_id=actor_id, task_id=task_id,
            after_sequence=after_sequence, limit=limit,
        )
        updates = []
        for event in events:
            payload = event.payload if isinstance(event.payload, dict) else {}
            kind = event.kind
            percent = None
            if kind == "task.created":
                name = self._safe_text(payload.get("name"), fallback="task")
                message = f"Started task: {name}"
            elif kind == "task.progress":
                message = self._safe_text(payload.get("message"), fallback="Task progress updated.")
                candidate = payload.get("percent")
                if type(candidate) is int and 0 <= candidate <= 100:
                    percent = candidate
            elif kind == "task.artifact":
                artifact_type = self._safe_text(payload.get("artifact_type"), fallback="file")
                message = f"Created a {artifact_type} artifact."
            elif kind == "task.completed":
                message = "Task completed."
                percent = 100
            elif kind == "task.failed":
                code = self._safe_text(payload.get("error_code"), fallback="unknown")
                message = f"Task failed ({code})."
            elif kind == "task.cancelled":
                message = "Task cancelled."
            elif kind == "task.interrupted":
                message = "Task interrupted. It was not automatically replayed."
            else:
                continue
            updates.append(CompanionTaskUpdate(
                task_id=event.task_id, sequence=event.sequence,
                kind=kind, message=message, percent=percent,
            ))
        return updates
