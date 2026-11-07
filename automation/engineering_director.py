"""Human-approved engineering task decomposition and model recommendations.

This director is deliberately deterministic: it proposes work from explicit
rules and reviewed registries. It does not call an LLM, dispatch tasks, or
activate providers. Submission requires an explicit approval argument.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from automation.agent_coordinator import SpecialistAgentCoordinator, SpecialistAgent, SubtaskSpec, AgentPlan
from core.model_capabilities import ModelCapabilityRegistry, classify_task
from core.local_model_registry import LocalModel
from core.model_selection import BenchmarkEvidence, LocalModelCandidate, select_local_models


@dataclass(frozen=True)
class EngineeringTaskProposal:
    subtask_id: str
    description: str
    required_capabilities: tuple[str, ...]
    depends_on: tuple[str, ...]
    acceptance_criteria: tuple[str, ...]
    assigned_agent_ids: tuple[str, ...]
    model_candidates: tuple[LocalModelCandidate, ...]


@dataclass(frozen=True)
class EngineeringProposal:
    goal: str
    classification: dict
    tasks: tuple[EngineeringTaskProposal, ...]
    requires_operator_approval: bool = True


class EngineeringDirector:
    """Create explainable proposal drafts and submit only after operator approval."""

    def __init__(
        self,
        coordinator: SpecialistAgentCoordinator,
        *,
        capability_registry: ModelCapabilityRegistry | None = None,
        local_models: Iterable[LocalModel] = (),
        benchmark_evidence: Iterable[BenchmarkEvidence] = (),
        available_memory_gib: float | None = None,
        minimum_benchmark_score: float = 0.70,
    ):
        if not isinstance(coordinator, SpecialistAgentCoordinator):
            raise TypeError("coordinator must be a SpecialistAgentCoordinator.")
        if capability_registry is not None and not isinstance(capability_registry, ModelCapabilityRegistry):
            raise TypeError("capability_registry must be a ModelCapabilityRegistry.")
        if available_memory_gib is not None and available_memory_gib <= 0:
            raise ValueError("available_memory_gib must be positive or None.")
        if not 0 <= minimum_benchmark_score <= 1:
            raise ValueError("minimum_benchmark_score must be between 0 and 1.")
        self.coordinator = coordinator
        self.capability_registry = capability_registry
        self.local_models = tuple(local_models)
        self.benchmark_evidence = tuple(benchmark_evidence)
        self.available_memory_gib = available_memory_gib
        self.minimum_benchmark_score = minimum_benchmark_score

    def propose(self, goal: str, *, explicit_task_type: str | None = None) -> EngineeringProposal:
        classification = classify_task(goal, explicit_task_type=explicit_task_type)
        required = tuple(classification["required_capabilities"])
        tasks = []
        previous = ()
        for index, capability in enumerate(required, 1):
            task_id = f"work-{index:02d}"
            description = (
                f"Goal: {goal.strip()}\n"
                f"Specialist focus: {capability}.\n"
                "Return the proposed artifact, supporting reasoning, assumptions, "
                "and unresolved risks. Do not claim unperformed execution or verification."
            )
            eligible = tuple(sorted(
                agent.agent_id for agent in self.coordinator.agents.values()
                if agent.enabled and capability in agent.capabilities
            ))
            if not eligible:
                raise ValueError(f"no enabled specialist is registered for capability: {capability}")
            criteria = (
                "Output directly addresses the stated goal and specialist focus.",
                "Assumptions and unknowns are explicitly identified.",
                "No unperformed actions, tests, or external verification are claimed.",
            )
            model_candidates = ()
            if self.capability_registry is not None and self.local_models:
                model_candidates = tuple(select_local_models(
                    capability, self.local_models, self.capability_registry,
                    self.benchmark_evidence,
                    available_memory_gib=self.available_memory_gib,
                    minimum_score=self.minimum_benchmark_score,
                ))
            tasks.append(EngineeringTaskProposal(
                task_id, description, (capability,), previous, criteria, eligible,
                model_candidates,
            ))
            previous = (task_id,)
        return EngineeringProposal(goal.strip(), classification, tuple(tasks))

    def submit_approved(
        self,
        proposal: EngineeringProposal,
        *,
        actor_id: str,
        idempotency_key: str,
        approved: bool,
    ) -> AgentPlan:
        if not isinstance(proposal, EngineeringProposal):
            raise TypeError("proposal must be an EngineeringProposal.")
        if approved is not True:
            raise PermissionError("operator approval is required before plan submission.")
        specs = tuple(SubtaskSpec(
            subtask_id=task.subtask_id,
            description=task.description + "\nAcceptance criteria:\n- " + "\n- ".join(task.acceptance_criteria),
            required_capabilities=task.required_capabilities,
            depends_on=task.depends_on,
            requires_approval=True,
        ) for task in proposal.tasks)
        return self.coordinator.submit(
            actor_id=actor_id, idempotency_key=idempotency_key, subtasks=specs,
        )
