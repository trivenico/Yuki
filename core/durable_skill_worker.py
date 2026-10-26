"""Safe bridge from durable tasks to the registered-skill approval runtime.

Only the fixed skill.invoke task type is supported. This worker never
interprets task payloads as code or shell commands. Tasks are single-attempt:
after an uncertain execution outcome they must be reviewed and resubmitted,
not automatically replayed.
"""
from __future__ import annotations

from typing import Any


class DurableSkillWorker:
    def __init__(self, engine: Any, runtime: Any, *, worker_id: str):
        if not isinstance(worker_id, str) or not worker_id or len(worker_id) > 256:
            raise ValueError("worker_id must be non-empty bounded text")
        self.engine = engine
        self.runtime = runtime
        self.worker_id = worker_id

    def run_once(self) -> dict[str, Any]:
        task = self.engine.claim(self.worker_id)
        if task is None:
            return {"state": "idle", "status": "no_task"}

        task_id = task["id"]
        # Side-effecting skill calls must not be automatically retried after an
        # ambiguous failure. Require the submitter to set max_attempts=1.
        if task.get("task_type") != "skill.invoke" or task.get("max_attempts") != 1:
            self.engine.cancel_task(task_id)
            return {
                "task_id": task_id,
                "state": "cancelled",
                "status": "unsupported_task",
            }

        payload = task.get("payload")
        if (
            not isinstance(payload, dict)
            or set(payload) != {"skill_name", "argument"}
            or not isinstance(payload.get("skill_name"), str)
            or not payload["skill_name"]
            or len(payload["skill_name"]) > 128
            or not isinstance(payload.get("argument"), str)
            or len(payload["argument"]) > 20000
        ):
            self.engine.cancel_task(task_id)
            return {
                "task_id": task_id,
                "state": "cancelled",
                "status": "invalid_payload",
            }

        skill_name = payload["skill_name"]
        argument = payload["argument"]
        try:
            result = self.runtime.invoke(skill_name, argument)
            if result.ok:
                # Persist only status metadata, never skill output which may
                # contain private data or credentials.
                completed = self.engine.complete(
                    task_id,
                    self.worker_id,
                    {
                        "ok": True,
                        "status": result.status,
                        "skill_name": skill_name,
                    },
                )
                return {
                    "task_id": task_id,
                    "state": completed["state"],
                    "status": result.status,
                    "message": result.message,
                }

            failed = self.engine.fail(
                task_id,
                self.worker_id,
                result.status or "skill invocation failed",
                retry_delay_seconds=0,
            )
            return {
                "task_id": task_id,
                "state": failed["state"],
                "status": result.status,
                "message": result.message,
            }
        except Exception:
            # If a provider/skill or persistence operation fails after a side
            # effect may have occurred, cancel instead of blindly replaying.
            try:
                current = self.engine.get_task(task_id)
                if current.get("state") == "running":
                    self.engine.cancel_task(task_id)
            except Exception:
                pass
            return {
                "task_id": task_id,
                "state": "cancelled",
                "status": "worker_error",
                "message": "Worker failed safely; inspect the audit events before resubmitting.",
            }
