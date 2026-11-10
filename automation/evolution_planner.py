"""Approval-first planning boundary for supervised Yuki self-evolution.

This layer accepts a structured proposal and delegates capability routing and
durable idempotency to SpecialistAgentCoordinator. It never dispatches tasks.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import re
from typing import Iterable

from automation.agent_coordinator import (
    AgentPlan,
    SpecialistAgentCoordinator,
    SubtaskSpec,
)

_REQUEST_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}$")
_MAX_OBJECTIVE = 4000
_MAX_SUBTASKS = 20


@dataclass(frozen=True)
class EvolutionProposal:
    objective: str
    request_id: str
    subtasks: tuple[SubtaskSpec, ...]


@dataclass(frozen=True)
class EvolutionPlan:
    plan: AgentPlan
    objective_digest: str
    approval_required: bool
    dispatch_started: bool


class EvolutionPlanner:
    """Validate and persist an approval-required plan without dispatching work."""

    def __init__(self, coordinator: SpecialistAgentCoordinator):
        if not isinstance(coordinator, SpecialistAgentCoordinator):
            raise TypeError("coordinator must be a SpecialistAgentCoordinator.")
        self.coordinator = coordinator

    @staticmethod
    def _validate(proposal: EvolutionProposal) -> tuple[str, tuple[SubtaskSpec, ...]]:
        if not isinstance(proposal, EvolutionProposal):
            raise TypeError("proposal must be an EvolutionProposal.")
        if not isinstance(proposal.objective, str):
            raise ValueError("objective must be text.")
        objective = proposal.objective.strip()
        if not objective or len(objective) > _MAX_OBJECTIVE or "\x00" in objective:
            raise ValueError("objective must be non-empty bounded text without NUL bytes.")
        if not isinstance(proposal.request_id, str) or not _REQUEST_ID.fullmatch(proposal.request_id):
            raise ValueError("request_id must be a bounded stable identifier.")
        if not isinstance(proposal.subtasks, tuple) or not 1 <= len(proposal.subtasks) <= _MAX_SUBTASKS:
            raise ValueError(f"subtasks must be a tuple containing 1-{_MAX_SUBTASKS} entries.")
        normalized = []
        for item in proposal.subtasks:
            if not isinstance(item, SubtaskSpec):
                raise ValueError("all subtasks must be SubtaskSpec values.")
            # Evolution is never silently dispatched as unattended work. Approval
            # remains mandatory even if a caller supplied a weaker subtask flag.
            normalized.append(SubtaskSpec(
                subtask_id=item.subtask_id,
                description=item.description,
                required_capabilities=item.required_capabilities,
                depends_on=item.depends_on,
                requires_approval=True,
                required_permissions=item.required_permissions,
            ))
        return objective, tuple(normalized)

    def plan(self, *, actor_id: str, proposal: EvolutionProposal) -> EvolutionPlan:
        objective, subtasks = self._validate(proposal)
        digest = hashlib.sha256(objective.encode("utf-8")).hexdigest()
        # Bind the durable idempotency key to both caller request ID and objective.
        # A changed objective cannot accidentally retrieve an unrelated old plan.
        idempotency_key = f"evolution:{proposal.request_id}:{digest}"
        plan = self.coordinator.submit(
            actor_id=actor_id,
            idempotency_key=idempotency_key,
            subtasks=subtasks,
        )
        if any(not item.requires_approval for item in plan.subtasks):
            raise RuntimeError("coordinator returned an evolution task without approval.")
        return EvolutionPlan(
            plan=plan,
            objective_digest=digest,
            approval_required=True,
            dispatch_started=False,
        )
