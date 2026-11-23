"""Fail-closed validation for operator-composed gate-first worker deployments."""
from __future__ import annotations

from types import MappingProxyType

from automation.specialist_worker_supervisor import SpecialistWorkerSupervisor
from core.gate_first_sandbox_adapter import GateFirstSandboxExecutorAdapter
from core.gate_first_sandbox_executor import GateFirstDockerSandboxExecutor
from core.sandbox_executor import SandboxExecutionProfile


def validate_gate_first_supervisor(supervisor: SpecialistWorkerSupervisor) -> SpecialistWorkerSupervisor:
    """Reject supervisors that can execute sandbox work outside the gated path."""
    if not isinstance(supervisor, SpecialistWorkerSupervisor):
        raise TypeError("gate-first deployment factory must return SpecialistWorkerSupervisor.")
    if not supervisor.workers:
        raise RuntimeError("gate-first deployment requires at least one registered worker.")
    for registered in supervisor.workers:
        worker = registered.worker
        executor = getattr(worker, "sandbox_executor", None)
        profiles = getattr(worker, "sandbox_profiles", None)
        if not isinstance(executor, GateFirstSandboxExecutorAdapter):
            raise RuntimeError("gate-first deployment refused: every worker must use GateFirstSandboxExecutorAdapter.")
        if not isinstance(executor.executor, GateFirstDockerSandboxExecutor):
            raise RuntimeError("gate-first deployment refused: adapter has no GateFirstDockerSandboxExecutor.")
        executor.executor.validate_deployment_host()
        if not isinstance(profiles, MappingProxyType) or not profiles:
            raise RuntimeError("gate-first deployment refused: every worker must have immutable sandbox profiles.")
        if any(
            not isinstance(profile, SandboxExecutionProfile) or spec_id != profile.spec_id
            for spec_id, profile in profiles.items()
        ):
            raise RuntimeError("gate-first deployment refused: sandbox profile registry is inconsistent.")
    return supervisor
