"""Opt-in local wiring for Yuki's durable specialist worker runtime.

The local operator identity is a process-local trust boundary, not a hosted
multi-user authentication mechanism. Only the interactive local dashboard
should receive the operator controls.
"""
from __future__ import annotations

from dataclasses import dataclass
import hmac
from pathlib import Path
from secrets import token_urlsafe

from automation.agent_coordinator import SpecialistAgent, SpecialistAgentCoordinator
from automation.managed_worker_service import ManagedWorkerService
from automation.specialist_worker import (
    SpecialistTaskWorker,
    build_handlers_from_provider_manager,
    build_provider_specialist_handlers,
)
from automation.task_runtime import TaskRuntime
from automation.worker_supervisor import WorkerSupervisor
from automation.workflow_store import WorkflowRunStore

LOCAL_OPERATOR_ACTOR = "local-operator"
WORKER_SERVICE_ACTOR = "yuki-worker-service"

_OPERATOR_ACTIONS = frozenset({"task:create", "task:list", "task:read", "task:approve", "task:cancel", "task:session:revoke"})
_WORKER_ACTIONS = frozenset({"task:complete", "task:fail", "task:recover"})


@dataclass(frozen=True)
class LocalWorkerRuntime:
    runtime: TaskRuntime
    supervisor: WorkerSupervisor
    service: ManagedWorkerService
    coordinator: SpecialistAgentCoordinator
    operator_actor_id: str
    approval_session_id: str


@dataclass(frozen=True)
class SessionWorkerRuntime:
    """Worker service bound to an existing owner-authorized task runtime."""

    runtime: TaskRuntime
    supervisor: WorkerSupervisor
    service: ManagedWorkerService
    owner_actor_id: str
    approval_session_id: str


def build_session_worker_runtime(
    runtime: TaskRuntime,
    provider,
    *,
    owner_actor_id: str,
    approval_session_id: str,
    max_tasks_per_cycle: int = 3,
    max_workers: int = 2,
) -> SessionWorkerRuntime:
    """Attach a bounded worker to the caller's existing store and live owner session.

    Unlike build_local_worker_runtime(), this function never creates a database,
    operator identity, approval token, or parallel coordinator. Worker-only
    actions are authorized only while the exact approving owner session remains
    live. The caller must stop/drain this service before revoking that session.
    """
    if not isinstance(runtime, TaskRuntime):
        raise TypeError("runtime must be a TaskRuntime.")
    if not isinstance(owner_actor_id, str) or not owner_actor_id.strip() or len(owner_actor_id) > 200:
        raise ValueError("owner_actor_id must be bounded non-empty text.")
    if not isinstance(approval_session_id, str) or not approval_session_id.strip() or len(approval_session_id) > 512:
        raise ValueError("approval_session_id must be bounded non-empty text.")
    if not callable(runtime.authorize) or not callable(runtime.verify_session):
        raise ValueError("the existing task runtime must have authorization and session verification.")
    if type(max_tasks_per_cycle) is not int or not 1 <= max_tasks_per_cycle <= 10:
        raise ValueError("max_tasks_per_cycle must be between 1 and 10.")
    if type(max_workers) is not int or not 1 <= max_workers <= 4:
        raise ValueError("max_workers must be between 1 and 4.")

    base_authorize = runtime.authorize

    def authorize(actor_id: str, action: str, task_id: str) -> bool:
        if actor_id == WORKER_SERVICE_ACTOR:
            if action not in _WORKER_ACTIONS:
                return False
            try:
                return runtime.verify_session(owner_actor_id, approval_session_id) is True
            except Exception:
                return False
        try:
            return base_authorize(actor_id, action, task_id) is True
        except Exception:
            return False

    # TaskRuntime is the shared authorization boundary for all operations. Keep
    # the owner authorizer intact and add only the private worker principal.
    runtime.authorize = authorize
    handlers = build_provider_specialist_handlers(provider)
    worker = SpecialistTaskWorker(
        runtime,
        worker_id="yuki-desktop-worker",
        service_actor_id=WORKER_SERVICE_ACTOR,
        handlers=handlers,
        approval_session_id=approval_session_id,
    )
    supervisor = WorkerSupervisor(
        runtime, worker,
        max_tasks_per_cycle=max_tasks_per_cycle,
        recovery_limit=10,
        max_workers=max_workers,
    )
    return SessionWorkerRuntime(
        runtime=runtime,
        supervisor=supervisor,
        service=ManagedWorkerService(supervisor),
        owner_actor_id=owner_actor_id,
        approval_session_id=approval_session_id,
    )


def build_local_worker_runtime(
    workspace: str | Path,
    provider=None,
    *,
    provider_manager=None,
    provider_preferences=None,
    allow_cloud: bool = False,
    max_tasks_per_cycle: int = 3,
    max_workers: int = 2,
) -> LocalWorkerRuntime:
    """Build shared SQLite task state and provider-backed specialists.

    This factory creates no threads, scheduler, daemon, or automatic work.
    Provider calls happen only after the operator explicitly runs a cycle.
    """
    if provider is not None and provider_manager is not None:
        raise ValueError("Pass either provider or provider_manager, not both.")
    if provider is None and provider_manager is None:
        raise ValueError("A provider or provider_manager is required.")
    if provider_manager is None and (provider_preferences is not None or allow_cloud is not False):
        raise ValueError("provider_preferences and allow_cloud require provider_manager.")
    if type(allow_cloud) is not bool:
        raise ValueError("allow_cloud must be a boolean.")
    root = Path(workspace).expanduser()
    root.mkdir(parents=True, exist_ok=True)
    if type(max_tasks_per_cycle) is not int or not 1 <= max_tasks_per_cycle <= 10:
        raise ValueError("max_tasks_per_cycle must be between 1 and 10.")
    if type(max_workers) is not int or not 1 <= max_workers <= 4:
        raise ValueError("max_workers must be between 1 and 4.")

    def authorize(actor_id: str, action: str, task_id: str) -> bool:
        if actor_id == LOCAL_OPERATOR_ACTOR:
            return action in _OPERATOR_ACTIONS
        if actor_id == WORKER_SERVICE_ACTOR:
            return action in _WORKER_ACTIONS
        return False

    database_path = str(root / "yuki_tasks.sqlite3")
    approval_session_id = token_urlsafe(32)

    def verify_approval_session(actor_id: str, session_id: str) -> bool:
        return (
            actor_id == LOCAL_OPERATOR_ACTOR
            and isinstance(session_id, str)
            and hmac.compare_digest(session_id, approval_session_id)
        )

    runtime = TaskRuntime(
        WorkflowRunStore(database_path),
        authorize=authorize,
        verify_session=verify_approval_session,
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
    worker = SpecialistTaskWorker(
        runtime,
        worker_id="yuki-local-worker",
        service_actor_id=WORKER_SERVICE_ACTOR,
        handlers=handlers,
        approval_session_id=approval_session_id,
    )
    supervisor = WorkerSupervisor(
        runtime,
        worker,
        max_tasks_per_cycle=max_tasks_per_cycle,
        recovery_limit=10,
        max_workers=max_workers,
    )
    return LocalWorkerRuntime(
        runtime=runtime,
        supervisor=supervisor,
        service=ManagedWorkerService(supervisor),
        coordinator=coordinator,
        operator_actor_id=LOCAL_OPERATOR_ACTOR,
        approval_session_id=approval_session_id,
    )
