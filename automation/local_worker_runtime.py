"""Opt-in local wiring for Yuki's durable specialist worker runtime.

The local operator identity is a process-local trust boundary, not a hosted
multi-user authentication mechanism. Only the interactive local dashboard
should receive the operator controls.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from secrets import token_urlsafe
from threading import Lock

from automation.agent_coordinator import SpecialistAgent, SpecialistAgentCoordinator
from automation.specialist_worker import (
    SpecialistTaskWorker,
    build_handlers_from_provider_manager,
    build_provider_specialist_handlers,
)
from automation.task_runtime import TaskRuntime
from automation.worker_supervisor import WorkerSupervisor
from automation.workflow_store import WorkflowRunStore
from core.maintenance_fence import MaintenanceFence
from core.private_profile import ensure_private_profile_directory
from core.readiness import ResourceInventory
from core.resource_budget import WorkerBudget, calculate_worker_budget
from core.worker_health import write_worker_health

LOCAL_OPERATOR_ACTOR = "local-operator"
WORKER_SERVICE_ACTOR = "yuki-worker-service"

_OPERATOR_ACTIONS = frozenset({"task:create", "task:list", "task:read", "task:approve", "task:cancel"})
_WORKER_ACTIONS = frozenset({"task:complete", "task:fail", "task:recover"})


@dataclass
class ResourcePressureStatus:
    """Latest operator-readable memory guard observation for this process."""

    pause_threshold_bytes: int
    resume_threshold_bytes: int
    available_bytes: int | None = None
    paused: bool = False
    reason: str = "not_configured"


@dataclass(frozen=True)
class LocalWorkerRuntime:
    """Runtime facade; provider instances remain owned by the caller/provider manager.

    The runtime borrows provider adapters to build handlers. It never closes a
    borrowed provider during shutdown, because a manager may share it with
    other application services. The owner must drain this runtime before
    closing the provider manager or direct provider dependency.
    """

    runtime: TaskRuntime
    supervisor: WorkerSupervisor
    coordinator: SpecialistAgentCoordinator
    operator_actor_id: str
    approval_session_id: str
    worker_budget: WorkerBudget
    resource_status: ResourcePressureStatus

    def shutdown(self, *, timeout_seconds: float = 30.0) -> bool:
        """Retire this ephemeral approval session, then drain active worker cycles.

        Revocation is persisted before shutdown begins, so claimed tasks that
        have not passed their final authorization check are denied. A False
        result means a synchronous handler is still active: keep the runtime,
        store, and provider dependencies alive and retry shutdown later.
        """
        from math import isfinite

        if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)):
            raise TypeError("timeout_seconds must be a finite number")
        if not 0 <= timeout_seconds <= 3600 or not isfinite(timeout_seconds):
            raise ValueError("timeout_seconds must be between 0 and 3600 seconds")

        # Persist the marker first. This closes the claim-to-dispatch path for
        # work that has not yet crossed the provider boundary. Already-running
        # synchronous provider calls cannot be recalled; supervisor drain waits.
        self.runtime.revoke_session_approvals(self.approval_session_id)
        return self.supervisor.shutdown(timeout_seconds=timeout_seconds)


def build_local_worker_runtime(
    workspace: str | Path,
    provider=None,
    *,
    provider_manager=None,
    provider_preferences=None,
    allow_cloud: bool = False,
    max_tasks_per_cycle: int = 3,
    max_workers: int = 1,
    per_worker_memory_bytes: int | None = None,
    memory_reserve_bytes: int = 0,
    memory_resume_bytes: int | None = None,
) -> LocalWorkerRuntime:
    """Build shared SQLite task state and provider-backed specialists.

    Provider/provider_manager are borrowed dependencies, not transferred
    ownership. This function never closes them, including on partial startup
    failure: a provider manager may be shared with unrelated application
    services. The caller remains responsible for their lifecycle and must
    keep them alive until LocalWorkerRuntime.shutdown() has drained all cycles.

    SQLite connections are operation-scoped; no database connection is kept
    open by the returned runtime between calls. A failed build may leave the
    private profile directory and initialized schema files for later reuse,
    but it does not return a live worker or start background work.
    """

    if provider is not None and provider_manager is not None:
        raise ValueError("Pass either provider or provider_manager, not both.")
    if provider is None and provider_manager is None:
        raise ValueError("A provider or provider_manager is required.")
    if provider_manager is None and (provider_preferences is not None or allow_cloud is not False):
        raise ValueError("provider_preferences and allow_cloud require provider_manager.")
    if type(allow_cloud) is not bool:
        raise ValueError("allow_cloud must be a boolean.")
    # The local worker journal is security-sensitive state, not ordinary
    # project output. Reject shared/permissive workspaces rather than placing
    # task approvals and worker leases in a broadly accessible directory.
    root = ensure_private_profile_directory(Path(workspace).expanduser())
    maintenance_fence = MaintenanceFence(root)
    if type(max_tasks_per_cycle) is not int or not 1 <= max_tasks_per_cycle <= 10:
        raise ValueError("max_tasks_per_cycle must be between 1 and 10.")
    if type(max_workers) is not int or not 1 <= max_workers <= 4:
        raise ValueError("max_workers must be between 1 and 4.")
    if type(memory_reserve_bytes) is not int or memory_reserve_bytes < 0:
        raise ValueError("memory_reserve_bytes must be a non-negative integer.")
    if memory_resume_bytes is not None:
        if type(memory_resume_bytes) is not int or memory_resume_bytes < 0:
            raise ValueError("memory_resume_bytes must be a non-negative integer.")
        if memory_reserve_bytes <= 0 or memory_resume_bytes < memory_reserve_bytes:
            raise ValueError("memory_resume_bytes requires a positive reserve and must be at least the pause threshold.")
    resources = ResourceInventory.detect()
    worker_budget = calculate_worker_budget(
        logical_cpu_count=resources.effective_cpu_count,
        memory_effective_available_bytes=resources.memory_effective_available_bytes,
        requested_concurrency=max_workers,
        per_worker_memory_bytes=per_worker_memory_bytes,
        memory_reserve_bytes=memory_reserve_bytes,
    )

    def authorize(actor_id: str, action: str, task_id: str) -> bool:
        if actor_id == LOCAL_OPERATOR_ACTOR:
            return action in _OPERATOR_ACTIONS
        if actor_id == WORKER_SERVICE_ACTOR:
            return action in _WORKER_ACTIONS
        return False

    database_path = str(root / "yuki_tasks.sqlite3")
    # Treat schema setup across the workflow and coordinator tables as one
    # serialized startup phase. Both constructors take this same reentrant
    # fence internally; the outer lease prevents another cooperating runtime
    # from observing/interleaving a half-initialized profile. DDL remains
    # idempotent rather than transactionally rolled back across constructors.
    with maintenance_fence.lease():
        runtime = TaskRuntime(
            WorkflowRunStore(database_path, maintenance_fence=maintenance_fence),
            authorize=authorize,
        )
        coordinator = SpecialistAgentCoordinator(
            runtime,
            database_path,
            [
                SpecialistAgent(
                    agent_id=role,
                    name=f"Yuki {role.title()} Specialist",
                    capabilities=frozenset({role}),
                    action="emit",
                )
                for role in ("research", "coding", "testing", "reporting")
            ],
            authorize=lambda actor_id, action, _resource: (
                actor_id == LOCAL_OPERATOR_ACTOR
                and action in {"agent:plan", "agent:dispatch", "agent:read", "agent:cancel"}
            ),
            maintenance_fence=maintenance_fence,
        )
    if provider_manager is not None:
        handlers = build_handlers_from_provider_manager(
            provider_manager,
            {role: role for role in ("research", "coding", "testing", "reporting")},
            provider_preferences=provider_preferences,
            allow_cloud=allow_cloud,
        )
    else:
        handlers = build_provider_specialist_handlers(provider)
    # Hysteresis prevents worker cycles from rapidly toggling around a
    # single memory threshold. The state is process-local and starts unpaused;
    # an unknown reading pauses and remains paused until the resume threshold
    # is observed. A restart starts conservatively with a fresh measurement.
    resume_threshold = (
        memory_reserve_bytes
        if memory_resume_bytes is None
        else memory_resume_bytes
    )
    resource_status = ResourcePressureStatus(
        pause_threshold_bytes=memory_reserve_bytes,
        resume_threshold_bytes=resume_threshold,
        reason="not_configured" if memory_reserve_bytes <= 0 else "awaiting_first_cycle",
    )

    resource_guard_lock = Lock()

    def resource_guard() -> bool:
        if memory_reserve_bytes <= 0:
            return True
        # Serialize the observation and hysteresis transition. Concurrent
        # cycles must not race the process-local pause/recovery state.
        with resource_guard_lock:
            try:
                available = ResourceInventory.detect().memory_effective_available_bytes
            except Exception:
                available = None
            resource_status.available_bytes = available
            if resource_status.paused:
                if available is not None and available >= resume_threshold:
                    resource_status.paused = False
                    resource_status.reason = "recovered"
                else:
                    resource_status.reason = (
                        "memory_unavailable" if available is None
                        else "below_resume_threshold"
                    )
                    return False
            elif available is None or available < memory_reserve_bytes:
                resource_status.paused = True
                resource_status.reason = (
                    "memory_unavailable" if available is None
                    else "below_pause_threshold"
                )
                return False
            else:
                resource_status.reason = "within_budget"
            return True

    worker = SpecialistTaskWorker(
        runtime,
        worker_id="yuki-local-worker",
        service_actor_id=WORKER_SERVICE_ACTOR,
        handlers=handlers,
    )
    health_path = root / "worker-health.json"

    def publish_health(phase: str, details: dict) -> None:
        # Snapshot failures are isolated by WorkerSupervisor; task execution
        # and authorization never depend on this diagnostic file.
        write_worker_health(health_path, details={"phase": phase, **details})

    supervisor = WorkerSupervisor(
        runtime,
        worker,
        max_tasks_per_cycle=max_tasks_per_cycle,
        recovery_limit=10,
        max_workers=worker_budget.allowed_concurrency,
        resource_guard=resource_guard if memory_reserve_bytes > 0 else None,
        health_publisher=publish_health,
    )
    return LocalWorkerRuntime(
        runtime=runtime,
        supervisor=supervisor,
        coordinator=coordinator,
        operator_actor_id=LOCAL_OPERATOR_ACTOR,
        approval_session_id=token_urlsafe(32),
        worker_budget=worker_budget,
        resource_status=resource_status,
    )
