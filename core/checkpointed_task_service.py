"""CLI-facing persistence boundary for bounded approved tasks.

A whole ApprovedTaskAgent invocation is treated as one checkpointed unit in this
phase. A process interruption during that invocation is not auto-resumed; it must
be reconciled because individual skill side effects are not checkpointed here.
"""
from __future__ import annotations

from dataclasses import dataclass
import uuid

from core.checkpointed_task_runner import CheckpointedTaskRunner
from core.task_checkpoint_store import SQLiteTaskCheckpointStore, TaskCheckpoint


@dataclass(frozen=True)
class TaskExecutionOutcome:
    checkpoint: TaskCheckpoint
    message: str
    agent_status: str
    error_type: str | None = None


class CheckpointedTaskService:
    def __init__(self, store: SQLiteTaskCheckpointStore, task_agent) -> None:
        if not callable(getattr(task_agent, "run", None)):
            raise TypeError("task_agent must implement run(goal).")
        self.store = store
        self.task_agent = task_agent
        self.runner = CheckpointedTaskRunner(store)

    def execute(self, goal: str) -> TaskExecutionOutcome:
        task_id = uuid.uuid4().hex
        self.runner.create(task_id, goal, ["run approved task agent"])
        agent_result = {}

        def execute_agent_step(_description: str, _index: int):
            result = self.task_agent.run(goal)
            agent_result["status"] = str(getattr(result, "status", "unknown"))
            agent_result["message"] = str(getattr(result, "message", "Task returned no message"))[:4000]
            agent_result["ok"] = getattr(result, "ok", False) is True
            if agent_result["status"] != "completed" or not agent_result["ok"]:
                raise RuntimeError("Task agent did not complete successfully.")
            return {
                "status": agent_result["status"],
                "message": agent_result["message"],
                "ok": True,
                "steps": int(getattr(result, "steps", 0)),
            }

        try:
            checkpoint = self.runner.run(task_id, execute_agent_step)
            return TaskExecutionOutcome(
                checkpoint=checkpoint,
                message=agent_result.get("message", "Task completed."),
                agent_status=agent_result.get("status", "completed"),
            )
        except Exception as exc:
            checkpoint = self.store.get(task_id)
            if checkpoint is None:
                raise RuntimeError("Task checkpoint disappeared during execution.") from exc
            return TaskExecutionOutcome(
                checkpoint=checkpoint,
                message=agent_result.get("message", "Task interrupted; inspect its checkpoint before retrying."),
                agent_status=agent_result.get("status", "interrupted"),
                error_type=type(exc).__name__,
            )

    def list_incomplete(self, *, limit: int = 100) -> list[TaskCheckpoint]:
        return self.store.list_incomplete(limit=limit)
