"""Opt-in local wiring for Yuki's durable specialist worker runtime.

The local operator identity is a process-local trust boundary, not a hosted
multi-user authentication mechanism. Only the interactive local dashboard
should receive the operator controls.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from secrets import token_urlsafe

from automation.agent_coordinator import SpecialistAgent, SpecialistAgentCoordinator
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

_OPERATOR_ACTIONS = frozenset({"task:create", "task:list", "task:read", "task:approve", "task:cancel"})
_WORKER_ACTIONS = frozenset({"task:complete", "task:fail", "task:recover"})


@dataclass(frozen=True)
class LocalWorkerRuntime:
    runtime: TaskRuntime
    supervisor: WorkerSupervisor
    coordinator: SpecialistAgentCoordinator
    operator_actor_id: str
    approval_session_id: str


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
    runtime = TaskRuntime(
        WorkflowRunStore(database_path),
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
    )
    if provider is not None and provider_manager is not None:
        raise ValueError("Pass either provider or provider_manager, not both.")
    if provider_manager is not None:
        handlers = build_handlers_from_provider_manager(
            provider_manager,
            {role: role for role in ("research", "coding", "testing", "reporting")},
            provider_preferences=provider_preferences,
            allow_cloud=allow_cloud,
        )
    else:
        if provider is None:
            raise ValueError("A provider or provider_manager is required.")
        if provider_preferences is not None or allow_cloud is not False:
            raise ValueError("provider_preferences and allow_cloud require provider_manager.")
        handlers = build_provider_specialist_handlers(provider)
    worker = SpecialistTaskWorker(
        runtime,
        worker_id="yuki-local-worker",
        service_actor_id=WORKER_SERVICE_ACTOR,
        handlers=handlers,
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
        coordinator=coordinator,
        operator_actor_id=LOCAL_OPERATOR_ACTOR,
        approval_session_id=token_urlsafe(32),
    )
