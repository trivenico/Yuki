"""Durable lifecycle wrapper for bounded task operations."""

from __future__ import annotations

from typing import Any, Callable

from core.task_store import TaskStore


class DurableTaskRunner:
    """Persist task lifecycle around a callable without automatically replaying work."""

    def __init__(self, store: TaskStore) -> None:
        if not isinstance(store, TaskStore):
            raise TypeError("store must be a TaskStore")
        self.store = store

    def run(self, goal: str, operation: Callable[[str], Any]) -> tuple[str, Any]:
        if not callable(operation):
            raise TypeError("operation must be callable")
        task = self.store.create(goal)
        task_id = task["task_id"]
        self.store.transition(task_id, "running")
        try:
            result = operation(goal)
            ok = getattr(result, "ok", False) is True
            status = getattr(result, "status", "")
            persisted = {
                "ok": ok,
                "status": str(status)[:80],
                "steps": getattr(result, "steps", 0),
                "message": str(getattr(result, "message", ""))[:2000],
            }
            self.store.transition(task_id, "completed" if ok else "failed", result=persisted)
            return task_id, result
        except BaseException:
            # A shutdown may arrive during the operation or while committing its
            # result. Only mark unfinished work interrupted; never rewrite a
            # terminal result or replay a task with uncertain side effects.
            try:
                current = self.store.get(task_id)
                if current is not None and current["status"] in {"queued", "running"}:
                    self.store.transition(task_id, "interrupted")
            except Exception:
                # Preserve the original exception; startup recovery can reconcile
                # any task that remains queued/running.
                pass
            raise
