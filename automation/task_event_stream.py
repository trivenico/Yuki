"""Replayable, authorization-checked event batches and SSE framing for task clients.

Transport adapters (CLI, desktop, HTTP/SSE, WebSocket) consume this small
transport-neutral boundary. Durable ordering and authorization remain owned
by TaskRuntime and WorkflowRunStore.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Iterable

from automation.task_runtime import TaskEvent, TaskRuntime


@dataclass(frozen=True)
class EventBatch:
    task_id: str
    events: tuple[TaskEvent, ...]
    next_sequence: int
    has_more: bool


class TaskEventStream:
    """Read bounded durable event batches and encode standards-compliant SSE frames."""

    def __init__(self, runtime: TaskRuntime) -> None:
        if not isinstance(runtime, TaskRuntime):
            raise TypeError("runtime must be a TaskRuntime")
        self.runtime = runtime

    def read_batch(
        self, *, actor_id: str, task_id: str, after_sequence: int = 0,
        limit: int = 100,
    ) -> EventBatch:
        if type(after_sequence) is not int or after_sequence < 0:
            raise ValueError("after_sequence must be a non-negative integer")
        if type(limit) is not int or not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        # Ask for one extra record so clients can distinguish a full page from completion.
        events = self.runtime.events(
            actor_id=actor_id, task_id=task_id,
            after_sequence=after_sequence, limit=limit + 1,
        )
        page = tuple(events[:limit])
        return EventBatch(
            task_id=task_id,
            events=page,
            next_sequence=page[-1].sequence if page else after_sequence,
            has_more=len(events) > limit,
        )

    @staticmethod
    def encode_sse(events: Iterable[TaskEvent]) -> str:
        """Encode persisted events as SSE frames; JSON keeps payloads on one data line."""
        frames: list[str] = []
        for event in events:
            if not isinstance(event, TaskEvent):
                raise TypeError("events must contain TaskEvent values")
            payload = {
                "task_id": event.task_id,
                "sequence": event.sequence,
                "actor_id": event.actor_id,
                "payload": event.payload,
                "created_at": event.created_at,
            }
            data = json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
            frames.append(f"id: {event.sequence}\\nevent: {event.kind}\\ndata: {data}\\n\\n")
        return "".join(frames)

    def read_sse(
        self, *, actor_id: str, task_id: str, after_sequence: int = 0,
        limit: int = 100,
    ) -> tuple[EventBatch, str]:
        batch = self.read_batch(
            actor_id=actor_id, task_id=task_id,
            after_sequence=after_sequence, limit=limit,
        )
        return batch, self.encode_sse(batch.events)
