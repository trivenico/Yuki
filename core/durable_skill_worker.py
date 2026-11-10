"""Safe bridge from durable tasks to the registered-skill approval runtime.

Only the fixed skill.invoke task type is supported. This worker never
interprets task payloads as code or shell commands. Tasks are single-attempt:
after an uncertain execution outcome they must be reviewed and resubmitted,
not automatically replayed.
"""
from __future__ import annotations

from typing import Any
import math
import threading

from core.guarded_skill_runtime import SkillPolicyContext


class DurableSkillWorker:
    def __init__(
        self,
        engine: Any,
        runtime: Any,
        *,
        worker_id: str,
        lease_seconds: int = 60,
        heartbeat_interval_seconds: float = 20,
        allow_unprotected_runtime: bool = False,
    ):
        if not isinstance(worker_id, str) or not worker_id or len(worker_id) > 256:
            raise ValueError("worker_id must be non-empty bounded text")
        if isinstance(lease_seconds, bool) or not isinstance(lease_seconds, int) or not 2 <= lease_seconds <= 3600:
            raise ValueError("lease_seconds must be an integer from 2 to 3600")
        if (
            isinstance(heartbeat_interval_seconds, bool)
            or not isinstance(heartbeat_interval_seconds, (int, float))
            or not math.isfinite(heartbeat_interval_seconds)
            or heartbeat_interval_seconds <= 0
            or heartbeat_interval_seconds > min(lease_seconds / 2, 300)
        ):
            raise ValueError("heartbeat interval must be finite, positive, and no greater than half the lease or 300 seconds")
        if not isinstance(allow_unprotected_runtime, bool):
            raise TypeError("allow_unprotected_runtime must be a boolean")
        policy_engine = getattr(runtime, "policy_engine", None)
        policy_factory = getattr(runtime, "policy_request_factory", None)
        policy_configured = (
            callable(getattr(policy_engine, "evaluate", None))
            and callable(policy_factory)
        )
        if not policy_configured and not allow_unprotected_runtime:
            raise ValueError(
                "DurableSkillWorker requires a central policy engine and request factory; "
                "set allow_unprotected_runtime=True only for isolated legacy tests"
            )
        self.engine = engine
        self.runtime = runtime
        self.worker_id = worker_id
        self.lease_seconds = lease_seconds
        self.heartbeat_interval_seconds = float(heartbeat_interval_seconds)
        self.policy_required = policy_configured

    def _invoke_with_lease_heartbeat(
        self, task_id: str, skill_name: str, argument: str,
        policy_context: SkillPolicyContext | None = None,
    ):
        stop = threading.Event()
        heartbeat_errors: list[Exception] = []

        def renew_lease():
            while not stop.wait(self.heartbeat_interval_seconds):
                try:
                    self.engine.heartbeat(
                        task_id,
                        self.worker_id,
                        lease_seconds=self.lease_seconds,
                    )
                except Exception as error:
                    heartbeat_errors.append(error)
                    stop.set()
                    return

        heartbeat_thread = threading.Thread(
            target=renew_lease,
            name=f"yuki-lease-{task_id[:8]}",
            daemon=True,
        )
        heartbeat_thread.start()
        try:
            if policy_context is None:
                result = self.runtime.invoke(skill_name, argument)
            else:
                result = self.runtime.invoke(
                    skill_name, argument, policy_context=policy_context
                )
        finally:
            stop.set()
            heartbeat_thread.join(timeout=max(1.0, self.heartbeat_interval_seconds + 0.5))
        if heartbeat_errors:
            raise RuntimeError("task lease heartbeat failed") from heartbeat_errors[0]
        return result

    def run_once(self) -> dict[str, Any]:
        task = self.engine.claim(self.worker_id, lease_seconds=self.lease_seconds)
        if task is None:
            return {"state": "idle", "status": "no_task"}

        task_id = task["id"]
        policy_context = None
        if self.policy_required:
            owner_id = task.get("owner_id")
            goal_id = task.get("goal_id")
            if (
                not isinstance(owner_id, str) or not owner_id.strip()
                or not isinstance(goal_id, str) or not goal_id.strip()
            ):
                rejected = self.engine.reject(
                    task_id, self.worker_id, "missing_authenticated_owner"
                )
                return {
                    "task_id": task_id,
                    "state": rejected["state"],
                    "status": "missing_authenticated_owner",
                }
            try:
                policy_context = SkillPolicyContext(
                    principal_id=owner_id,
                    trigger="durable_task",
                    goal_id=goal_id,
                    task_id=task_id,
                )
            except ValueError:
                rejected = self.engine.reject(
                    task_id, self.worker_id, "invalid_authenticated_owner"
                )
                return {
                    "task_id": task_id,
                    "state": rejected["state"],
                    "status": "invalid_authenticated_owner",
                }
        # Side-effecting skill calls must not be automatically retried after an
        # ambiguous failure. Require the submitter to set max_attempts=1.
        if task.get("task_type") != "skill.invoke":
            rejected = self.engine.reject(
                task_id, self.worker_id, "unsupported_task_type"
            )
            return {
                "task_id": task_id,
                "state": rejected["state"],
                "status": "unsupported_task",
            }
        if task.get("max_attempts") != 1:
            rejected = self.engine.reject(
                task_id, self.worker_id, "unsafe_retry_policy"
            )
            return {
                "task_id": task_id,
                "state": rejected["state"],
                "status": "unsafe_retry_policy",
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
            rejected = self.engine.reject(
                task_id, self.worker_id, "invalid_payload"
            )
            return {
                "task_id": task_id,
                "state": rejected["state"],
                "status": "invalid_payload",
            }

        skill_name = payload["skill_name"]
        argument = payload["argument"]
        try:
            result = self._invoke_with_lease_heartbeat(
                task_id, skill_name, argument, policy_context
            )
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
            # effect may have occurred, cancel only if the durable task is still
            # running. If commit succeeded but its response failed, report the
            # persisted terminal state rather than inventing a cancellation.
            persisted_state = "unknown"
            try:
                current = self.engine.get_task(task_id)
                persisted_state = current.get("state", "unknown")
                if persisted_state == "running":
                    # This worker accepts only max_attempts=1, so record a
                    # terminal failure without permitting an automatic replay.
                    current = self.engine.fail(
                        task_id,
                        self.worker_id,
                        "worker invocation raised an exception",
                        retry_delay_seconds=0,
                    )
                    persisted_state = current.get("state", "failed")
            except Exception:
                pass
            return {
                "task_id": task_id,
                "state": persisted_state,
                "status": "worker_error",
                "message": "Worker encountered an error; inspect durable state and audit events before resubmitting.",
            }
