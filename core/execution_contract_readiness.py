"""Fail-closed matching of agent execution budgets to worker sandbox profiles.

This is a read-only preflight. Host-provided sandbox verification is an explicit
trust boundary; this module never treats Docker configuration as attestation.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from automation.agent_execution_spec import AgentExecutionSpec
from core.sandbox_executor import SandboxExecutionProfile


@dataclass(frozen=True, slots=True)
class WorkerExecutionCapacity:
    """Host-attested upper bounds for one currently eligible worker."""

    max_runtime_seconds: int
    max_memory_mb: int
    max_tokens: int
    max_steps: int

    def __post_init__(self) -> None:
        bounds = {
            "max_runtime_seconds": (1, 86_400),
            "max_memory_mb": (16, 65_536),
            "max_tokens": (1, 1_000_000),
            "max_steps": (1, 20),
        }
        for field, (minimum, maximum) in bounds.items():
            value = getattr(self, field)
            if type(value) is not int or not minimum <= value <= maximum:
                raise ValueError(f"{field} must be an integer from {minimum} to {maximum}.")


@dataclass(frozen=True, slots=True)
class ExecutionContractReadiness:
    status: str
    ready_for_approval: bool
    reasons: tuple[str, ...]
    spec_id: str | None
    sandbox_image: str | None


def assess_execution_contract(
    *,
    spec: AgentExecutionSpec | None,
    profile: SandboxExecutionProfile | None,
    worker_capacity: WorkerExecutionCapacity | None,
    sandbox_verified: bool | None,
) -> ExecutionContractReadiness:
    """Check spec/profile/budget compatibility without authorizing or running work.

    sandbox_verified must come from an operator-trusted, current host verifier.
    None means that the host has not supplied attestation; False means it failed.
    """
    if sandbox_verified is not None and type(sandbox_verified) is not bool:
        raise ValueError("sandbox_verified must be true, false, or unknown.")
    if spec is not None and not isinstance(spec, AgentExecutionSpec):
        raise TypeError("spec must be an AgentExecutionSpec or None.")
    if profile is not None and not isinstance(profile, SandboxExecutionProfile):
        raise TypeError("profile must be a SandboxExecutionProfile or None.")
    if worker_capacity is not None and not isinstance(worker_capacity, WorkerExecutionCapacity):
        raise TypeError("worker_capacity must be a WorkerExecutionCapacity or None.")

    reasons: list[str] = []
    if spec is None:
        reasons.append("A versioned execution specification is not available.")
    if profile is None:
        reasons.append("An operator-configured sandbox profile is not available.")
    if worker_capacity is None:
        reasons.append("Attested worker resource capacity is not available.")
    if sandbox_verified is None:
        reasons.append("Current host sandbox verification is not available.")
    elif sandbox_verified is False:
        reasons.append("Host sandbox verification failed.")

    if spec is not None and profile is not None:
        if profile.spec_id != spec.spec_id:
            reasons.append("Sandbox profile does not match the execution specification.")
        policy = profile.policy
        budget = spec.budget
        if policy.timeout_seconds > budget.max_runtime_seconds:
            reasons.append("Sandbox timeout exceeds the execution specification runtime budget.")
        if policy.memory_mb > budget.max_memory_mb:
            reasons.append("Sandbox memory limit exceeds the execution specification memory budget.")

    if spec is not None and worker_capacity is not None:
        budget = spec.budget
        if worker_capacity.max_runtime_seconds < budget.max_runtime_seconds:
            reasons.append("Worker runtime capacity is below the requested execution budget.")
        if worker_capacity.max_memory_mb < budget.max_memory_mb:
            reasons.append("Worker memory capacity is below the requested execution budget.")
        if worker_capacity.max_tokens < budget.max_tokens:
            reasons.append("Worker token capacity is below the requested execution budget.")
        if worker_capacity.max_steps < budget.max_steps:
            reasons.append("Worker step capacity is below the requested execution budget.")

    if not reasons:
        return ExecutionContractReadiness(
            status="ready_for_approval", ready_for_approval=True,
            reasons=(), spec_id=spec.spec_id, sandbox_image=profile.image,
        )

    review_required = (
        spec is None or profile is None or worker_capacity is None
        or sandbox_verified is None
        or any("not available" in reason for reason in reasons)
    )
    return ExecutionContractReadiness(
        status="review_required" if review_required else "blocked",
        ready_for_approval=False,
        reasons=tuple(reasons[:12]),
        spec_id=spec.spec_id if spec is not None else None,
        sandbox_image=profile.image if profile is not None else None,
    )
