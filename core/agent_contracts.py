"""Typed, fail-closed contracts for Yuki's specialist-agent registry.

This module describes agent identity and delegation authority only. It does not
execute tasks, grant operating-system permissions, or make tool calls.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import re
from threading import RLock
from typing import Iterable, Mapping

_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_.:-]{0,79}$")
_AGENT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$")
_MAX_DESCRIPTION = 500
_MAX_OBJECTIVE = 2_000
_MAX_PAYLOAD_CHARS = 12_000
_MAX_CAPABILITIES = 64


def _identifier_set(values: Iterable[str], field_name: str, *, maximum: int = _MAX_CAPABILITIES) -> frozenset[str]:
    if isinstance(values, (str, bytes)):
        raise ValueError(f"{field_name} must be a collection of identifiers.")
    try:
        items = tuple(values)
    except TypeError as exc:
        raise ValueError(f"{field_name} must be iterable.") from exc
    if len(items) > maximum:
        raise ValueError(f"{field_name} exceeds its limit.")
    if any(not isinstance(item, str) or not _IDENTIFIER.fullmatch(item) for item in items):
        raise ValueError(f"{field_name} contains an invalid identifier.")
    if len(set(items)) != len(items):
        raise ValueError(f"{field_name} contains duplicates.")
    return frozenset(items)


@dataclass(frozen=True, slots=True)
class AgentSpec:
    """Immutable declaration of one agent's identity and least-privilege scope."""

    agent_id: str
    role: str
    description: str
    capabilities: frozenset[str] = field(default_factory=frozenset)
    allowed_tools: frozenset[str] = field(default_factory=frozenset)
    provider_preferences: tuple[str, ...] = ()
    allow_cloud: bool = False
    max_concurrency: int | None = None
    timeout_seconds: int | None = None
    delegation_targets: frozenset[str] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        if not isinstance(self.agent_id, str) or not _AGENT_ID.fullmatch(self.agent_id):
            raise ValueError("agent_id must be a bounded identifier.")
        if not isinstance(self.role, str) or not _IDENTIFIER.fullmatch(self.role):
            raise ValueError("role must be a bounded identifier.")
        if not isinstance(self.description, str) or not self.description.strip() or len(self.description) > _MAX_DESCRIPTION:
            raise ValueError("description must be non-empty bounded text.")
        object.__setattr__(self, "capabilities", _identifier_set(self.capabilities, "capabilities"))
        object.__setattr__(self, "allowed_tools", _identifier_set(self.allowed_tools, "allowed_tools"))
        if isinstance(self.delegation_targets, (str, bytes)):
            raise ValueError("delegation_targets must be a collection.")
        targets = tuple(self.delegation_targets)
        if any(not isinstance(x, str) or not _AGENT_ID.fullmatch(x) for x in targets):
            raise ValueError("delegation_targets contains an invalid agent ID.")
        if len(set(targets)) != len(targets):
            raise ValueError("delegation_targets contains duplicates.")
        object.__setattr__(self, "delegation_targets", frozenset(targets))
        if not isinstance(self.provider_preferences, tuple) or len(self.provider_preferences) > 16:
            raise ValueError("provider_preferences must be a tuple of at most 16 providers.")
        normalized = []
        for provider in self.provider_preferences:
            if not isinstance(provider, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,39}", provider.strip().lower()):
                raise ValueError("provider_preferences contains an invalid provider.")
            normalized.append(provider.strip().lower())
        if len(set(normalized)) != len(normalized):
            raise ValueError("provider_preferences contains duplicates.")
        object.__setattr__(self, "provider_preferences", tuple(normalized))
        if type(self.allow_cloud) is not bool:
            raise ValueError("allow_cloud must be a boolean.")
        if self.max_concurrency is not None and (type(self.max_concurrency) is not int or self.max_concurrency < 1):
            raise ValueError("max_concurrency must be None (unlimited) or a positive integer.")
        if self.timeout_seconds is not None and (type(self.timeout_seconds) is not int or self.timeout_seconds < 1):
            raise ValueError("timeout_seconds must be None (no agent deadline) or a positive integer.")
        if self.delegation_targets and "agent:delegate" not in self.capabilities:
            raise ValueError("delegation targets require the agent:delegate capability.")


@dataclass(frozen=True, slots=True)
class DelegationRequest:
    """Bounded request envelope; payload is data, never executable instructions."""

    request_id: str
    task_id: str
    parent_agent_id: str
    target_agent_id: str
    objective: str
    required_capabilities: frozenset[str] = field(default_factory=frozenset)
    input_payload: str = ""

    def __post_init__(self) -> None:
        for name in ("request_id", "task_id"):
            value = getattr(self, name)
            if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,99}", value):
                raise ValueError(f"{name} must be a bounded identifier.")
        for name in ("parent_agent_id", "target_agent_id"):
            value = getattr(self, name)
            if not isinstance(value, str) or not _AGENT_ID.fullmatch(value):
                raise ValueError(f"{name} must be a valid agent ID.")
        if not isinstance(self.objective, str) or not self.objective.strip() or len(self.objective) > _MAX_OBJECTIVE or "\x00" in self.objective:
            raise ValueError("objective must be non-empty bounded text.")
        if not isinstance(self.input_payload, str) or len(self.input_payload) > _MAX_PAYLOAD_CHARS or "\x00" in self.input_payload:
            raise ValueError("input_payload must be bounded text.")
        object.__setattr__(self, "required_capabilities", _identifier_set(self.required_capabilities, "required_capabilities"))


class AgentRegistry:
    """Thread-safe in-memory registry with explicit delegation allow-lists.

    Persistence belongs in the durable registry/store adapter; this class is the
    validation and authorization boundary shared by those adapters.
    """

    def __init__(self, *, max_agents: int | None = None) -> None:
        if max_agents is not None and (type(max_agents) is not int or max_agents < 1):
            raise ValueError("max_agents must be None (unlimited) or a positive integer.")
        self._max_agents = max_agents
        self._agents: dict[str, AgentSpec] = {}
        self._lock = RLock()

    def register(self, spec: AgentSpec) -> AgentSpec:
        if not isinstance(spec, AgentSpec):
            raise TypeError("spec must be an AgentSpec.")
        with self._lock:
            if spec.agent_id in self._agents:
                raise ValueError("agent is already registered.")
            if self._max_agents is not None and len(self._agents) >= self._max_agents:
                raise ValueError("configured agent registry capacity reached.")
            unknown = spec.delegation_targets.difference(self._agents)
            if unknown:
                raise ValueError("delegation targets must be registered before the delegating agent.")
            self._agents[spec.agent_id] = spec
            return spec

    def get(self, agent_id: str) -> AgentSpec | None:
        if not isinstance(agent_id, str):
            return None
        with self._lock:
            return self._agents.get(agent_id)

    def list_agents(self) -> tuple[AgentSpec, ...]:
        with self._lock:
            return tuple(self._agents[key] for key in sorted(self._agents))

    def authorize_delegation(self, request: DelegationRequest) -> AgentSpec:
        """Return target spec only when source allow-list and target capabilities match."""
        if not isinstance(request, DelegationRequest):
            raise TypeError("request must be a DelegationRequest.")
        with self._lock:
            source = self._agents.get(request.parent_agent_id)
            target = self._agents.get(request.target_agent_id)
            if source is None or target is None:
                raise PermissionError("delegation references an unknown agent.")
            if "agent:delegate" not in source.capabilities:
                raise PermissionError("source agent cannot delegate.")
            if request.target_agent_id not in source.delegation_targets:
                raise PermissionError("target agent is not in the source delegation allow-list.")
            if not request.required_capabilities.issubset(target.capabilities):
                raise PermissionError("target agent lacks required capabilities.")
            return target
