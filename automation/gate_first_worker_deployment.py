"""Fail-closed validation for operator-composed gate-first worker deployments.

The operator factory remains responsible for constructing Docker Engine API,
host evidence, identity resolution, release publisher and per-spec profiles.
This module verifies that the returned supervisor actually routes every worker
through the gate-first adapter before the service starts claiming tasks.
"""
from __future__ import annotations

from automation.specialist_worker_supervisor import SpecialistWorkerSupervisor
from core.gate_first_sandbox_adapter import GateFirstSandboxExecutorAdapter


def validate_gate_first_supervisor(supervisor: SpecialistWorkerSupervisor) -> SpecialistWorkerSupervisor:
    """Reject a supervisor containing any worker without gate-first execution."""
    if not isinstance(supervisor, SpecialistWorkerSupervisor):
        raise TypeError("gate-first deployment factory must return SpecialistWorkerSupervisor.")
    if not supervisor.workers:
        raise RuntimeError("gate-first deployment requires at least one registered worker.")
    for registered in supervisor.workers:
        worker = registered.worker
        executor = getattr(worker, "sandbox_executor", None)
        profiles = getattr(worker, "sandbox_profiles", None)
        if not isinstance(executor, GateFirstSandboxExecutorAdapter):
            raise RuntimeError(
                "gate-first deployment refused: every worker must use GateFirstSandboxExecutorAdapter."
            )
        if not isinstance(profiles, dict) or not profiles:
            raise RuntimeError(
                "gate-first deployment refused: every worker must have immutable sandbox profiles."
            )
        if any(spec_id != profile.spec_id for spec_id, profile in profiles.items()):
            raise RuntimeError("gate-first deployment refused: sandbox profile registry is inconsistent.")
    return supervisor
