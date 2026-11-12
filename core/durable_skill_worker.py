"""Safe bridge from durable tasks to the registered-skill approval runtime.

Only the fixed skill.invoke task type is supported. This worker never
interprets task payloads as code or shell commands. Tasks are single-attempt:
after an uncertain execution outcome they must be reviewed and resubmitted,
not automatically replayed.
"""
from __future__ import annotations

from typing import Any, Callable
import hashlib
import math
import threading
import time

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
        approval_token_resolver: Callable[[SkillPolicyContext, str, str], str | None] | None = None,
        approval_wait_timeout_seconds: int = 86400,
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
        self.engine = engine
        self.runtime = runtime
        if approval_token_resolver is not None and not callable(approval_token_resolver):
            raise ValueError("approval_token_resolver must be callable")
        resolver_worker_id = getattr(approval_token_resolver, "worker_id", None)
        if resolver_worker_id is not None and resolver_worker_id != worker_id:
            raise ValueError("approval resolver is bound to a different worker identity")
        if getattr(runtime, "approval_gateway", None) is not None and approval_token_resolver is None:
            raise ValueError("durable approval gateway requires a trusted approval_token_resolver")
        if (
            isinstance(approval_wait_timeout_seconds, bool)
            or not isinstance(approval_wait_timeout_seconds, int)
            or not 1 <= approval_wait_timeout_seconds <= 86400
        ):
            raise ValueError("approval_wait_timeout_seconds must be an integer from 1 to 86400")
        self.approval_token_resolver = approval_token_resolver
        self.worker_id = worker_id
        self.lease_seconds = lease_seconds
        self.heartbeat_interval_seconds = float(heartbeat_interval_seconds)
        self.approval_wait_timeout_seconds = approval_wait_timeout_seconds

    def _invoke_with_lease_heartbeat(self, task_id: str, lease_token: str, skill_name: str, argument: str, policy_context: SkillPolicyContext):
        stop = threading.Event()
        heartbeat_errors: list[Exception] = []

        def renew_lease():
            while not stop.wait(self.heartbeat_interval_seconds):
                try:
                    self.engine.heartbeat(
                        task_id,
                        self.worker_id,
                        lease_token=lease_token,
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
            approval_token = None
            approval_request_id = None
            if self.approval_token_resolver is not None:
                if self.engine.validate_lease(task_id, self.worker_id, lease_token) is not True:
                    raise PermissionError("task claim is no longer valid before approval lookup")
                policy_engine = getattr(self.runtime, "policy_engine", None)
                request_factory = getattr(self.runtime, "policy_request_factory", None)
                gateway = getattr(self.runtime, "approval_gateway", None)
                requires_owner_approval = False
                if policy_engine is not None and callable(request_factory) and gateway is not None:
                    skill = self.runtime.registry.get(skill_name)
                    if skill is None:
                        raise PermissionError("registered skill is unavailable for policy review")
                    policy_request = request_factory(skill_name, argument, skill, policy_context)
                    decision = policy_engine.evaluate(policy_request)
                    decision_name = getattr(getattr(decision, "decision", None), "value", None)
                    requires_owner_approval = decision_name == "require_approval"
                    if requires_owner_approval:
                        approval_request_id = gateway.request_approval(policy_request)
                        self.engine.record_approval_wait(
                            task_id, self.worker_id, lease_token, approval_request_id,
                        )
                if requires_owner_approval:
                    deadline = time.monotonic() + self.approval_wait_timeout_seconds
                    while True:
                        if self.engine.validate_lease(task_id, self.worker_id, lease_token) is not True:
                            raise PermissionError("task claim changed while awaiting owner approval")
                        approval_token = self.approval_token_resolver(
                            policy_context, skill_name, lease_token
                        )
                        if approval_request_id is not None:
                            request_status = gateway.get_request_status(approval_request_id)
                            if request_status in {"rejected", "expired"}:
                                claim_digest = hashlib.sha256(lease_token.encode("ascii")).hexdigest()
                                self.engine.transition_approval_wait(
                                    task_id, claim_digest, request_status,
                                )
                                raise PermissionError("owner did not approve this operation")
                        if heartbeat_errors:
                            raise RuntimeError("task lease heartbeat failed while awaiting approval")
                        if approval_token is not None:
                            if not isinstance(approval_token, str) or not 40 <= len(approval_token) <= 256:
                                raise PermissionError("approval resolver returned an invalid credential")
                            break
                        if time.monotonic() >= deadline:
                            raise TimeoutError("owner approval did not arrive before the wait deadline")
                        stop.wait(min(0.5, max(0.01, deadline - time.monotonic())))
                    if self.engine.validate_lease(task_id, self.worker_id, lease_token) is not True:
                        raise PermissionError("task claim changed during approval delivery")
                    if requires_owner_approval:
                        claim_digest = hashlib.sha256(lease_token.encode("ascii")).hexdigest()
                        wait = self.engine.get_approval_wait(task_id, claim_digest)
                        if wait is None or wait["state"] != "delivered":
                            raise PermissionError("durable approval wait has not been delivered to this claim")
                elif policy_engine is None:
                    # Legacy one-shot resolver behavior remains non-blocking;
                    # only central durable approvals create a supervised wait.
                    approval_token = self.approval_token_resolver(
                        policy_context, skill_name, lease_token
                    )
                    if approval_token is not None and (
                        not isinstance(approval_token, str) or not 40 <= len(approval_token) <= 256
                    ):
                        raise PermissionError("approval resolver returned an invalid credential")
            invoke_kwargs = {
                "execution_guard": lambda: self.engine.validate_lease(task_id, self.worker_id, lease_token),
                "policy_context": policy_context,
            }
            if approval_token is not None:
                invoke_kwargs["approval_token"] = approval_token
            result = self.runtime.invoke(skill_name, argument, **invoke_kwargs)
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
        lease_token = task.get("lease_token")
        if not isinstance(lease_token, str) or len(lease_token) < 32:
            raise RuntimeError("durable task claim did not provide a valid fencing token")
        # Derive principal identity from the durable goal while validating this
        # exact live claim. Never trust owner IDs from task payloads or model output.
        try:
            identity = self.engine.get_claim_identity(task_id, self.worker_id, lease_token)
        except Exception:
            # A stale, expired or unreadable claim must not crash the worker loop
            # and must never fall back to caller-supplied identity.
            return {
                "task_id": task_id,
                "state": "running",
                "status": "claim_identity_unavailable",
                "message": "Claim identity could not be verified; no skill was dispatched.",
            }
        if (
            not isinstance(identity, dict)
            or set(identity) != {"owner_id", "goal_id", "task_id"}
            or identity.get("task_id") != task_id
            or identity.get("goal_id") != task.get("goal_id")
        ):
            return {
                "task_id": task_id,
                "state": "running",
                "status": "invalid_claim_identity",
                "message": "Durable claim identity did not match the claimed task; no skill was dispatched.",
            }
        owner_id = identity.get("owner_id")
        if not isinstance(owner_id, str) or not owner_id or len(owner_id) > 256:
            try:
                rejected = self.engine.reject(
                    task_id, self.worker_id, "unowned_goal", lease_token=lease_token
                )
            except Exception:
                return {
                    "task_id": task_id,
                    "state": "unknown",
                    "status": "claim_lost_during_rejection",
                    "message": "Unowned task could not be rejected under the current claim.",
                }
            return {
                "task_id": task_id,
                "state": rejected["state"],
                "status": "unowned_goal",
            }
        try:
            policy_context = SkillPolicyContext(
                principal_id=owner_id,
                trigger="durable_task",
                goal_id=identity["goal_id"],
                task_id=identity["task_id"],
                claim_token_sha256=hashlib.sha256(lease_token.encode("ascii")).hexdigest(),
            )
        except (KeyError, TypeError, ValueError):
            return {
                "task_id": task_id,
                "state": "running",
                "status": "invalid_claim_identity",
                "message": "Durable claim identity was malformed; no skill was dispatched.",
            }
        # Side-effecting skill calls must not be automatically retried after an
        # ambiguous failure. Require the submitter to set max_attempts=1.
        if task.get("task_type") != "skill.invoke":
            rejected = self.engine.reject(
                task_id, self.worker_id, "unsupported_task_type", lease_token=lease_token
            )
            return {
                "task_id": task_id,
                "state": rejected["state"],
                "status": "unsupported_task",
            }
        if task.get("max_attempts") != 1:
            rejected = self.engine.reject(
                task_id, self.worker_id, "unsafe_retry_policy", lease_token=lease_token
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
                task_id, self.worker_id, "invalid_payload", lease_token=lease_token
            )
            return {
                "task_id": task_id,
                "state": rejected["state"],
                "status": "invalid_payload",
            }

        skill_name = payload["skill_name"]
        argument = payload["argument"]
        try:
            result = self._invoke_with_lease_heartbeat(task_id, lease_token, skill_name, argument, policy_context)
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
                    lease_token=lease_token,
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
                lease_token=lease_token,
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
                        lease_token=lease_token,
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
