"""Worker-protocol adapter for the opt-in gate-first Docker executor."""
from __future__ import annotations

from typing import Callable

from automation.agent_execution_spec import AgentExecutionSpec
from core.gate_first_sandbox_executor import GateFirstDockerSandboxExecutor, GateFirstExecutionError
from core.sandbox_executor import SandboxExecutionProfile, SandboxExecutionResult


class GateFirstSandboxExecutorAdapter:
    """Adapt gate-first execution to SpecialistTaskWorker's authorization contract.

    This adapter never falls back to DockerSandboxExecutor. Authorization is
    checked before the gate executor allocates resources; lifecycle start is
    emitted only after exact argv exec observation by the gate session.
    """

    def __init__(self, executor: GateFirstDockerSandboxExecutor) -> None:
        if not isinstance(executor, GateFirstDockerSandboxExecutor):
            raise TypeError("executor must be a GateFirstDockerSandboxExecutor")
        self.executor = executor

    def execute_authorized_task(
        self, *, task: object, execution_spec: AgentExecutionSpec,
        profile: SandboxExecutionProfile, actor_id: str, worker_id: str,
        authorize: Callable[[str, str, str], bool],
        cancellation_check: Callable[[], bool] | None = None,
        lifecycle_callback: Callable[[str, str, SandboxExecutionResult | None], None] | None = None,
    ) -> SandboxExecutionResult:
        task_id = getattr(task, "run_id", None) or getattr(task, "task_id", None)
        if (
            not isinstance(execution_spec, AgentExecutionSpec)
            or not isinstance(profile, SandboxExecutionProfile)
            or profile.spec_id != execution_spec.spec_id
            or getattr(task, "agent_execution_spec_id", None) != execution_spec.spec_id
            or getattr(task, "lease_owner", None) != worker_id
            or getattr(task, "state", None) != "running"
            or not isinstance(task_id, str) or not task_id
            or not isinstance(actor_id, str) or not actor_id
            or not isinstance(worker_id, str) or not worker_id
            or not callable(authorize)
        ):
            raise GateFirstExecutionError("authorized task, lease, profile or authorization contract is invalid")
        try:
            permitted = authorize(actor_id, "task:sandbox:execute", task_id) is True
        except Exception:
            permitted = False
        if not permitted:
            raise PermissionError("sandbox task execution is not authorized")
        return self.executor.execute(
            task=task,
            worker_id=worker_id,
            execution_spec=execution_spec,
            profile=profile,
            cancellation_check=cancellation_check,
            lifecycle_callback=lifecycle_callback,
        )
