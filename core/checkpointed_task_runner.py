"""Checkpointed task orchestration primitives.

The runner records progress after each step. A step exception is treated as an
uncertain outcome and moves the task to needs_reconciliation; callers must not
blindly replay that step. Steps are injected by the trusted application layer.
"""
from __future__ import annotations

from typing import Any, Callable
from core.task_checkpoint_store import SQLiteTaskCheckpointStore, TaskCheckpoint


class CheckpointedTaskRunner:
    def __init__(self, store: SQLiteTaskCheckpointStore) -> None:
        if not callable(getattr(store, "create", None)) or not callable(getattr(store, "checkpoint", None)):
            raise TypeError("A task checkpoint store is required.")
        self.store = store

    @staticmethod
    def _task_payload(goal: str, steps: list[str]) -> dict[str, Any]:
        if not isinstance(goal, str) or not goal.strip() or len(goal) > 4000:
            raise ValueError("goal must be non-empty text up to 4000 characters.")
        if not isinstance(steps, list) or not steps or len(steps) > 100:
            raise ValueError("steps must contain 1 to 100 step descriptions.")
        if any(not isinstance(step, str) or not step.strip() or len(step) > 1000 for step in steps):
            raise ValueError("each step must be non-empty text up to 1000 characters.")
        return {
            "goal": goal,
            "steps": list(steps),
            "completed_steps": [],
            "current_step": 0,
            "results": [],
        }

    def create(self, task_id: str, goal: str, steps: list[str]) -> TaskCheckpoint:
        return self.store.create(task_id, self._task_payload(goal, steps), status="pending")

    def run(self, task_id: str, execute_step: Callable[[str, int], Any]) -> TaskCheckpoint:
        """Execute remaining steps; exceptions stop progress and require reconciliation.

        execute_step must be supplied by trusted code and must return JSON-safe data.
        The runner deliberately does not retry a failed step.
        """
        if not callable(execute_step):
            raise TypeError("execute_step must be callable.")
        checkpoint = self.store.get(task_id)
        if checkpoint is None:
            raise KeyError("Task checkpoint does not exist.")
        if checkpoint.status in {"completed", "failed"}:
            return checkpoint
        if checkpoint.status == "needs_reconciliation":
            raise RuntimeError("Task requires reconciliation before execution can resume.")
        if checkpoint.status not in {"pending", "paused", "running"}:
            raise RuntimeError("Task is not in a runnable state.")
        payload = checkpoint.payload
        steps = payload.get("steps")
        completed = payload.get("completed_steps")
        index = payload.get("current_step")
        results = payload.get("results")
        if (not isinstance(steps, list) or not isinstance(completed, list)
                or type(index) is not int or not isinstance(results, list)
                or index != len(completed) or index != len(results)
                or not 0 <= index <= len(steps)):
            raise ValueError("Task checkpoint progress is inconsistent; refusing execution.")
        checkpoint = self.store.checkpoint(
            task_id, expected_revision=checkpoint.revision, payload=payload, status="running"
        )
        for index in range(index, len(steps)):
            # A crash after the side effect but before the following checkpoint is
            # indistinguishable from an uncertain outcome; reconcile, never auto-replay.
            try:
                result = execute_step(steps[index], index)
                next_completed = completed + [index]
                next_results = results + [result]
                next_payload = dict(payload)
                next_payload.update({
                    "completed_steps": next_completed,
                    "current_step": index + 1,
                    "results": next_results,
                })
                checkpoint = self.store.checkpoint(
                    task_id, expected_revision=checkpoint.revision,
                    payload=next_payload,
                    status="completed" if index + 1 == len(steps) else "running",
                )
                payload, completed, results = next_payload, next_completed, next_results
            except Exception:
                latest = self.store.get(task_id)
                if latest is not None and latest.status not in {"completed", "failed", "needs_reconciliation"}:
                    try:
                        self.store.checkpoint(
                            task_id, expected_revision=latest.revision,
                            payload=latest.payload, status="needs_reconciliation",
                        )
                    except Exception:
                        # Preserve the original execution failure. If persistence also
                        # failed, callers must treat the task state as uncertain.
                        pass
                raise
        return checkpoint
