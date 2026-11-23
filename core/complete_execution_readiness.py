"""Composite execution readiness: budgets, sandbox attestation and host environment."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from automation.agent_execution_spec import AgentExecutionSpec
from core.execution_contract_readiness import (
    WorkerExecutionCapacity, assess_execution_contract,
)
from core.runtime_environment_readiness import (
    RuntimeEnvironmentEvidence, RuntimeEnvironmentReadiness,
    RuntimeEnvironmentRequirements, assess_runtime_environment,
)
from core.sandbox_executor import SandboxExecutionProfile


@dataclass(frozen=True, slots=True)
class CompleteExecutionReadiness:
    status: str
    ready_for_approval: bool
    reasons: tuple[str, ...]
    contract_status: str
    environment_status: str
    runtime_verifier_id: str | None


def assess_complete_execution_readiness(
    *,
    spec: AgentExecutionSpec | None,
    profile: SandboxExecutionProfile | None,
    worker_capacity: WorkerExecutionCapacity | None,
    sandbox_verified: bool | None,
    task_id: str,
    runtime_requirements: RuntimeEnvironmentRequirements | None,
    runtime_evidence: RuntimeEnvironmentEvidence | None,
    runtime_evidence_verifier,
    now: datetime | None = None,
    expected_worker_id: str | None = None,
) -> CompleteExecutionReadiness:
    """Require both preflights; this remains advisory and never authorizes execution."""
    contract = assess_execution_contract(
        spec=spec, profile=profile, worker_capacity=worker_capacity,
        sandbox_verified=sandbox_verified,
    )
    environment = assess_runtime_environment(
        spec=spec, profile=profile, task_id=task_id,
        requirements=runtime_requirements, evidence=runtime_evidence,
        evidence_verifier=runtime_evidence_verifier, now=now,
        expected_worker_id=expected_worker_id,
    )
    reasons = tuple(
        [f"Execution contract: {reason}" for reason in contract.reasons]
        + [f"Runtime environment: {reason}" for reason in environment.reasons]
    )[:32]
    if contract.status == "blocked" or environment.status == "blocked":
        status = "blocked"
    elif contract.status == "review_required" or environment.status == "review_required":
        status = "review_required"
    else:
        status = "ready_for_approval"
    return CompleteExecutionReadiness(
        status=status,
        ready_for_approval=status == "ready_for_approval",
        reasons=reasons,
        contract_status=contract.status,
        environment_status=environment.status,
        runtime_verifier_id=environment.verifier_id,
    )
