"""Deterministic capability-based routing for supervised Yuki tasks.

This module selects an explicitly registered skill-backed agent for each
validated planner task. It does not execute skills, grant permissions, or
bypass the existing approval runtime.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Iterable

from core.planner import PlanTask, Planner


_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_.-]{0,79}$")
_CAPABILITY = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")


class AgentRoutingError(ValueError):
    """Base error for invalid or unroutable agent plans."""


class DuplicateAgentError(AgentRoutingError):
    """Raised when an agent ID is registered more than once."""


class CapabilityUnavailable(AgentRoutingError):
    """Raised when no registered agent satisfies a task's capabilities."""


@dataclass(frozen=True)
class AgentSpec:
    """Reviewed routing metadata; permissions remain enforced elsewhere."""

    agent_id: str
    skill_name: str
    capabilities: tuple[str, ...]
    description: str = ""
    max_concurrent_tasks: int = 1

    def __post_init__(self) -> None:
        if not isinstance(self.agent_id, str) or not _IDENTIFIER.fullmatch(self.agent_id):
            raise ValueError("agent_id must be a bounded lowercase identifier")
        if not isinstance(self.skill_name, str) or not _IDENTIFIER.fullmatch(self.skill_name):
            raise ValueError("skill_name must be a bounded lowercase identifier")
        if not isinstance(self.capabilities, tuple) or not self.capabilities:
            raise ValueError("capabilities must be a non-empty tuple")
        if any(not isinstance(item, str) or not _CAPABILITY.fullmatch(item)
               for item in self.capabilities):
            raise ValueError("capabilities must be bounded lowercase identifiers")
        if len(set(self.capabilities)) != len(self.capabilities):
            raise ValueError("capabilities must be unique")
        if not isinstance(self.description, str) or len(self.description) > 1000:
            raise ValueError("description must be bounded text")
        if (isinstance(self.max_concurrent_tasks, bool)
                or not isinstance(self.max_concurrent_tasks, int)
                or not 1 <= self.max_concurrent_tasks <= 100):
            raise ValueError("max_concurrent_tasks must be between 1 and 100")


@dataclass(frozen=True)
class RoutedTask:
    """A planned assignment, not an authorization or execution receipt."""

    task_id: str
    agent_id: str
    skill_name: str
    required_capabilities: tuple[str, ...]
    depends_on: tuple[str, ...]
    requires_approval: bool


class AgentRegistry:
    """In-memory registry populated only from trusted application config."""

    def __init__(self, agents: Iterable[AgentSpec] = ()) -> None:
        self._agents: dict[str, AgentSpec] = {}
        for agent in agents:
            self.register(agent)

    def register(self, agent: AgentSpec) -> None:
        if not isinstance(agent, AgentSpec):
            raise TypeError("only AgentSpec instances may be registered")
        if agent.agent_id in self._agents:
            raise DuplicateAgentError(f"agent already registered: {agent.agent_id}")
        self._agents[agent.agent_id] = agent

    def get(self, agent_id: str) -> AgentSpec | None:
        return self._agents.get(agent_id)

    def list_agents(self) -> tuple[AgentSpec, ...]:
        return tuple(self._agents[key] for key in sorted(self._agents))

    def candidates(self, required: Iterable[str]) -> tuple[AgentSpec, ...]:
        required_set = frozenset(required)
        return tuple(
            agent for agent in self.list_agents()
            if required_set.issubset(agent.capabilities)
        )


class AgentOrchestrator:
    """Validate a task DAG and deterministically route each task to an agent.

    Routing is deliberately separate from execution. The caller must pass the
    resulting skill invocation through Yuki's existing guarded approval runtime.
    """

    def __init__(self, registry: AgentRegistry, *, max_tasks: int = 10) -> None:
        if not isinstance(registry, AgentRegistry):
            raise TypeError("registry must be an AgentRegistry")
        if (isinstance(max_tasks, bool) or not isinstance(max_tasks, int)
                or not 1 <= max_tasks <= 10):
            raise ValueError("max_tasks must be between 1 and 10")
        self.registry = registry
        self.max_tasks = max_tasks

    def route(self, tasks: Iterable[PlanTask]) -> tuple[RoutedTask, ...]:
        validated = Planner.validate_tasks(tasks, max_steps=self.max_tasks)
        routed: list[RoutedTask] = []
        for task in validated:
            candidates = self.registry.candidates(task.required_capabilities)
            if not candidates:
                requested = ", ".join(task.required_capabilities)
                raise CapabilityUnavailable(
                    f"no registered agent satisfies task {task.task_id}: {requested}"
                )
            # Prefer the narrowest capability set, then stable agent ID. This
            # avoids granting a broader specialist assignment when unnecessary.
            selected = min(
                candidates,
                key=lambda agent: (len(agent.capabilities), agent.agent_id),
            )
            routed.append(RoutedTask(
                task_id=task.task_id,
                agent_id=selected.agent_id,
                skill_name=selected.skill_name,
                required_capabilities=task.required_capabilities,
                depends_on=task.depends_on,
                requires_approval=task.requires_approval,
            ))
        return tuple(routed)
