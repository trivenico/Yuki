"""Supervised worker for tasks assigned through the Yuki workforce registry.

The executor is deliberately injected: it must enforce current permissions and
human approval through Yuki's guarded skill/tool runtime before any side effect.
This adapter never executes model output, shell commands, or arbitrary code.
"""
from __future__ import annotations

from typing import Any
import math
import threading

from core.durable_task_engine import DurableTaskEngine
from core.workforce import AgentRegistry


class WorkforceWorker:
    """Claim workforce tasks, revalidate employee policy, then delegate safely."""

    def __init__(
        self,
        engine: DurableTaskEngine,
        registry: AgentRegistry,
        *,
        worker_id: str,
        executor: Any,
        reviewer: Any = None,
        lease_seconds: int = 60,
        heartbeat_interval_seconds: float = 20,
    ):
        if not isinstance(engine, DurableTaskEngine):
            raise TypeError("engine must be a DurableTaskEngine")
        if not isinstance(registry, AgentRegistry):
            raise TypeError("registry must be an AgentRegistry")
        if not isinstance(worker_id, str) or not worker_id.strip() or len(worker_id) > 256:
            raise ValueError("worker_id must be non-empty bounded text")
        if not callable(getattr(executor, "authorize_and_execute", None)):
            raise TypeError("executor must implement authorize_and_execute()")
        if isinstance(lease_seconds, bool) or not isinstance(lease_seconds, int) or not 2 <= lease_seconds <= 3600:
            raise ValueError("lease_seconds must be an integer from 2 to 3600")
        if (isinstance(heartbeat_interval_seconds, bool)
                or not isinstance(heartbeat_interval_seconds, (int, float))
                or not math.isfinite(heartbeat_interval_seconds)
                or heartbeat_interval_seconds <= 0
                or heartbeat_interval_seconds > min(lease_seconds / 2, 300)):
            raise ValueError("heartbeat interval must be finite, positive, and no greater than half the lease or 300 seconds")
        self.engine = engine
        self.registry = registry
        self.worker_id = worker_id
        if reviewer is not None and not callable(getattr(reviewer, "review", None)):
            raise TypeError("reviewer must implement review()")
        self.executor = executor
        self.reviewer = reviewer
        self.lease_seconds = lease_seconds
        self.heartbeat_interval_seconds = float(heartbeat_interval_seconds)

    def _execute_with_heartbeat(self, **kwargs):
        stop = threading.Event()
        errors = []

        def renew():
            while not stop.wait(self.heartbeat_interval_seconds):
                try:
                    self.engine.heartbeat(
                        kwargs["task_id"], self.worker_id,
                        lease_seconds=self.lease_seconds,
                    )
                except Exception as error:
                    errors.append(error)
                    stop.set()
                    return

        thread = threading.Thread(target=renew, name="yuki-workforce-lease", daemon=True)
        thread.start()
        try:
            outcome = self.executor.authorize_and_execute(**kwargs)
        finally:
            stop.set()
            thread.join(timeout=max(1.0, self.heartbeat_interval_seconds + 0.5))
        if errors:
            raise RuntimeError("workforce task lease heartbeat failed") from errors[0]
        return outcome

    def _review_with_heartbeat(self, **kwargs):
        stop = threading.Event()
        errors = []

        def renew():
            while not stop.wait(self.heartbeat_interval_seconds):
                try:
                    self.engine.heartbeat(
                        kwargs["task_id"], self.worker_id,
                        lease_seconds=self.lease_seconds,
                    )
                except Exception as error:
                    errors.append(error)
                    stop.set()
                    return

        thread = threading.Thread(target=renew, name="yuki-workforce-review-lease", daemon=True)
        thread.start()
        try:
            decision = self.reviewer.review(**kwargs["review_context"])
        finally:
            stop.set()
            thread.join(timeout=max(1.0, self.heartbeat_interval_seconds + 0.5))
        if errors:
            raise RuntimeError("workforce review lease heartbeat failed") from errors[0]
        return decision

    def run_once(self) -> dict[str, Any]:
        task = self.engine.claim(self.worker_id, lease_seconds=self.lease_seconds, task_type="workforce.task")
        if task is None:
            return {"state": "idle", "status": "no_task"}
        task_id = task["id"]

        if task.get("task_type") != "workforce.task":
            rejected = self.engine.reject(task_id, self.worker_id, "unsupported_task_type")
            return {"task_id": task_id, "state": rejected["state"], "status": "unsupported_task"}

        if task.get("max_attempts") != 1:
            rejected = self.engine.reject(task_id, self.worker_id, "unsafe_retry_policy")
            return {"task_id": task_id, "state": rejected["state"], "status": "unsafe_retry_policy"}

        payload = task.get("payload")
        expected = {
            "description", "assigned_employee_id", "required_capabilities",
            "required_permissions", "max_autonomy_tier", "execution_authorized",
            "review_required",
        }
        if (
            not isinstance(payload, dict)
            or set(payload) != expected
            or not isinstance(payload.get("description"), str)
            or not payload["description"].strip()
            or len(payload["description"]) > 20_000
            or not isinstance(payload.get("assigned_employee_id"), str)
            or not isinstance(payload.get("required_capabilities"), list)
            or not payload["required_capabilities"]
            or any(not isinstance(x, str) or not x.strip() for x in payload["required_capabilities"])
            or not isinstance(payload.get("required_permissions"), list)
            or any(not isinstance(x, str) or not x.strip() for x in payload["required_permissions"])
            or type(payload.get("max_autonomy_tier")) is not int
            or payload["max_autonomy_tier"] not in range(4)
            or payload.get("execution_authorized") is not False
            or type(payload.get("review_required")) is not bool
        ):
            rejected = self.engine.reject(task_id, self.worker_id, "invalid_payload")
            return {"task_id": task_id, "state": rejected["state"], "status": "invalid_payload"}

        employee = self.registry.get(payload["assigned_employee_id"])
        required_capabilities = frozenset(payload["required_capabilities"])
        required_permissions = frozenset(payload["required_permissions"])
        if (
            employee is None
            or employee.status != "available"
            or employee.active_tasks >= employee.max_concurrent_tasks
            or employee.autonomy_tier > payload["max_autonomy_tier"]
            or not required_capabilities.issubset(employee.capabilities)
            or not required_permissions.issubset(employee.permissions)
        ):
            rejected = self.engine.reject(task_id, self.worker_id, "invalid_payload")
            return {"task_id": task_id, "state": rejected["state"], "status": "employee_policy_changed"}

        try:
            # This is the only execution seam. The injected executor must perform
            # fresh authorization and obtain any required approval; registry
            # assignment and this worker are never treated as approval.
            outcome = self._execute_with_heartbeat(
                employee=employee,
                description=payload["description"],
                required_capabilities=tuple(sorted(required_capabilities)),
                required_permissions=tuple(sorted(required_permissions)),
                max_autonomy_tier=payload["max_autonomy_tier"],
                task_id=task_id,
            )
            ok = getattr(outcome, "ok", None)
            status = getattr(outcome, "status", None)
            if type(ok) is not bool or not isinstance(status, str) or not status:
                raise TypeError("executor returned an invalid outcome")
            if ok and payload["review_required"]:
                if self.reviewer is None:
                    failed = self.engine.fail(task_id, self.worker_id, "independent review unavailable")
                    return {"task_id": task_id, "state": failed["state"], "status": "reviewer_unavailable"}
                reviewer_id = getattr(self.reviewer, "reviewer_id", None)
                if not isinstance(reviewer_id, str) or not reviewer_id.strip():
                    failed = self.engine.fail(task_id, self.worker_id, "reviewer identity unavailable")
                    return {"task_id": task_id, "state": failed["state"], "status": "reviewer_identity_unavailable"}
                if reviewer_id == employee.employee_id:
                    failed = self.engine.fail(task_id, self.worker_id, "self review is prohibited")
                    return {"task_id": task_id, "state": failed["state"], "status": "self_review_denied"}
                review = self._review_with_heartbeat(
                    task_id=task_id,
                    review_context={
                        "description": payload["description"],
                        "employee_id": employee.employee_id,
                        "execution_status": status,
                        "execution_message": str(getattr(outcome, "message", ""))[:2000],
                    },
                )
                approved = getattr(review, "approved", None)
                review_status = getattr(review, "status", None)
                if type(approved) is not bool or not isinstance(review_status, str) or not review_status:
                    raise TypeError("reviewer returned an invalid decision")
                if not approved:
                    failed = self.engine.fail(task_id, self.worker_id, "independent review rejected")
                    return {"task_id": task_id, "state": failed["state"], "status": "review_rejected"}
                status = f"{status}:reviewed"
            if ok:
                completed = self.engine.complete(task_id, self.worker_id, {
                    "ok": True, "status": status, "employee_id": employee.employee_id,
                    "review_required": payload["review_required"],
                })
                return {"task_id": task_id, "state": completed["state"], "status": status}
            failed = self.engine.fail(task_id, self.worker_id, status)
            return {"task_id": task_id, "state": failed["state"], "status": status}
        except Exception:
            # Side effects may have happened before an exception; single-attempt
            # tasks are failed, never automatically replayed.
            try:
                current = self.engine.get_task(task_id)
                if current.get("state") == "running":
                    current = self.engine.fail(task_id, self.worker_id, "workforce executor failed")
                state = current.get("state", "unknown")
            except Exception:
                state = "unknown"
            return {
                "task_id": task_id,
                "state": state,
                "status": "worker_error",
                "message": "Inspect approval/audit evidence before any manual resubmission.",
            }
