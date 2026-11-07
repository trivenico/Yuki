"""Opt-in local wiring for Yuki's durable specialist worker runtime.

The local operator identity is a process-local trust boundary, not a hosted
multi-user authentication mechanism. Only the interactive local dashboard
should receive the operator controls.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping
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
from core.reasoning.execution_budget_store import ExecutionBudgetStore
from automation.verification_evidence_store import VerificationEvidenceStore
from automation.verification_workflow import VerificationWorkflow
from automation.engineering_acceptance import EngineeringAcceptanceStore
from automation.local_verification_executor import LocalVerificationExecutor, VerificationCommand

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
    budget_store: ExecutionBudgetStore
    verification_evidence_store: VerificationEvidenceStore
    verification_workflow: VerificationWorkflow
    acceptance_store: EngineeringAcceptanceStore
    verification_executor: LocalVerificationExecutor | None


def build_local_worker_runtime(
    workspace: str | Path,
    provider=None,
    *,
    provider_manager=None,
    provider_preferences=None,
    allow_cloud: bool = False,
    max_tasks_per_cycle: int = 3,
    max_workers: int = 2,
    verification_commands: Mapping[str, VerificationCommand] | None = None,
    verification_workspace: str | Path | None = None,
    verification_reviewer_authorizer=None,
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
    if verification_reviewer_authorizer is not None and not callable(verification_reviewer_authorizer):
        raise TypeError("verification_reviewer_authorizer must be callable.")
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
    budget_store = ExecutionBudgetStore(database_path)
    verification_executor = None
    if verification_commands is not None:
        execution_root = Path(verification_workspace).expanduser().resolve() if verification_workspace is not None else root.resolve()
        if not execution_root.is_dir():
            raise ValueError("verification_workspace_must_exist")
        verification_executor = LocalVerificationExecutor(
            execution_root, root / "yuki_verification_executions.sqlite3",
            verification_commands,
        )
    verification_evidence_store = VerificationEvidenceStore(
        root / "yuki_verification.sqlite3",
        execution_evidence_validator=(
            verification_executor.validate_evidence_reference
            if verification_executor is not None else None
        ),
        reviewer_authorizer=verification_reviewer_authorizer,
    )
    verification_workflow = VerificationWorkflow(
        root / "yuki_artifacts.sqlite3",
        verification_evidence_store,
        reviewer_authorize=lambda actor_id, action, _artifact: (
            actor_id == LOCAL_OPERATOR_ACTOR and action == "verification:submit"
        ),
    )
    acceptance_store = EngineeringAcceptanceStore(
        root / "yuki_acceptance.sqlite3",
        verification_workflow,
        authorize=lambda actor_id, action, _plan: (
            actor_id == LOCAL_OPERATOR_ACTOR and action.startswith("acceptance:")
        ),
    )

    def budget_policy(kind: str, plan_id: str, subtask_id: str | None):
        if kind == "plan" and subtask_id is None:
            return {
                "max_model_turns": 100,
                "max_tool_calls": 20,
                "max_duration_ms": 1_200_000,
                "max_transcript_chars": 200_000,
            }
        if kind == "subtask" and isinstance(subtask_id, str):
            return {
                "max_model_turns": 5,
                "max_tool_calls": 1,
                "max_duration_ms": 60_000,
                "max_transcript_chars": 10_000,
            }
        raise ValueError("unsupported budget policy scope.")

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
            and action in {"agent:plan", "agent:plan_without_approval", "agent:dispatch", "agent:read", "agent:cancel"}
        ),
        budget_store=budget_store,
        budget_policy=budget_policy,
        budget_policy_version="local-v1",
        acceptance_store=acceptance_store,
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
        budget_store=budget_store,
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
        budget_store=budget_store,
        verification_evidence_store=verification_evidence_store,
        verification_workflow=verification_workflow,
        acceptance_store=acceptance_store,
        verification_executor=verification_executor,
    )
