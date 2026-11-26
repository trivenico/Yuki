"""Safe bridge from durable tasks to the registered-skill approval runtime.

Only the fixed skill.invoke task type is supported. This worker never
interprets task payloads as code or shell commands. Tasks are single-attempt:
after an uncertain execution outcome they must be reviewed and resubmitted,
not automatically replayed.
"""
from __future__ import annotations

from typing import Any, Callable
import hashlib
import json
import math
import threading

from core.authorization import AuthorizationRequest, Principal
from core.authorization_gateway import AuthorizationGateway


class DurableSkillWorker:
    def __init__(
        self,
        engine: Any,
        runtime: Any,
        *,
        worker_id: str,
        lease_seconds: int = 60,
        heartbeat_interval_seconds: float = 20,
        authorization_gateway: AuthorizationGateway,
        principal_for_worker: Callable[[str], Principal],
        policy_revision: str | None = None,
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
        if not isinstance(authorization_gateway, AuthorizationGateway):
            raise TypeError("a central AuthorizationGateway is required")
        if not callable(principal_for_worker):
            raise TypeError("a trusted workload principal resolver is required")
        if policy_revision is not None and (
            not isinstance(policy_revision, str) or not policy_revision.strip()
            or len(policy_revision) > 128
        ):
            raise ValueError("policy_revision must be non-empty bounded text")
        self.engine = engine
        self.runtime = runtime
        self.worker_id = worker_id
        self.lease_seconds = lease_seconds
        self.heartbeat_interval_seconds = float(heartbeat_interval_seconds)
        self.authorization_gateway = authorization_gateway
        self.principal_for_worker = principal_for_worker
        self.policy_revision = policy_revision

    def _resolve_workload_principal(self) -> Principal:
        """Resolve identity from trusted host credentials, never from worker_id alone."""
        principal = self.principal_for_worker(self.worker_id)
        if (
            not isinstance(principal, Principal)
            or principal.kind != "workload"
            or principal.tenant_id is None
            or principal.agent_id is None
        ):
            raise PermissionError("authenticated workload identity is unavailable")
        return principal

    def _authorize_invocation(self, task_id: str, skill_name: str, argument: str) -> None:
        """Resolve authenticated workload identity and audit exact skill intent."""
        principal = self._resolve_workload_principal()
        argument_digest = hashlib.sha256(argument.encode("utf-8")).hexdigest()
        envelope = json.dumps(
            {"task_id": task_id, "skill_name": skill_name, "argument_sha256": argument_digest},
            sort_keys=True, separators=(",", ":"), ensure_ascii=True,
        ).encode("utf-8")
        fingerprint = hashlib.sha256(envelope).hexdigest()
        self.authorization_gateway.require(AuthorizationRequest(
            principal=principal,
            action="skill:invoke",
            resource="skill-invocation:sha256:" + fingerprint,
            risk="high",
            task_id=task_id,
            expected_policy_revision=self.policy_revision,
        ))

    def _invoke_with_lease_heartbeat(self, task_id: str, lease_generation: str, skill_name: str, argument: str):
        # Reconfirm and extend ownership immediately before entering skill code.
        # If the lease expired or was stolen after authorization, do not invoke.
        self.engine.heartbeat(
            task_id, self.worker_id, lease_generation=lease_generation, lease_seconds=self.lease_seconds
        )
        stop = threading.Event()
        heartbeat_errors: list[Exception] = []

        def renew_lease():
            while not stop.wait(self.heartbeat_interval_seconds):
                try:
                    self.engine.heartbeat(
                        task_id,
                        self.worker_id,
                        lease_generation=lease_generation,
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
            result = self.runtime.invoke(skill_name, argument)
        finally:
            stop.set()
            heartbeat_thread.join(timeout=max(1.0, self.heartbeat_interval_seconds + 0.5))
        if heartbeat_errors:
            raise RuntimeError("task lease heartbeat failed") from heartbeat_errors[0]
        return result

    def run_once(self) -> dict[str, Any]:
        # Authenticate before claiming work so an untrusted process cannot
        # consume or reject queued tasks merely by guessing a worker label.
        self._resolve_workload_principal()
        task = self.engine.claim(self.worker_id, lease_seconds=self.lease_seconds)
        if task is None:
            return {"state": "idle", "status": "no_task"}

        task_id = task["id"]
        lease_generation = task["lease_generation"]
        # Side-effecting skill calls must not be automatically retried after an
        # ambiguous failure. Require the submitter to set max_attempts=1.
        if task.get("task_type") != "skill.invoke":
            rejected = self.engine.reject(
                task_id, self.worker_id, "unsupported_task_type", lease_generation=lease_generation
            )
            return {
                "task_id": task_id,
                "state": rejected["state"],
                "status": "unsupported_task",
            }
        if task.get("max_attempts") != 1:
            rejected = self.engine.reject(
                task_id, self.worker_id, "unsafe_retry_policy", lease_generation=lease_generation
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
                task_id, self.worker_id, "invalid_payload", lease_generation=lease_generation
            )
            return {
                "task_id": task_id,
                "state": rejected["state"],
                "status": "invalid_payload",
            }

        skill_name = payload["skill_name"]
        argument = payload["argument"]
        try:
            # The central gateway is mandatory for every durable skill call.
            # It runs after claim/payload validation and immediately before
            # entering the skill runtime; local one-shot approval remains a
            # separate, additional check inside that runtime.
            self._authorize_invocation(task_id, skill_name, argument)
            result = self._invoke_with_lease_heartbeat(task_id, lease_generation, skill_name, argument)
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
                    lease_generation=lease_generation,
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
                lease_generation=lease_generation,
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
                        lease_generation=lease_generation,
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
