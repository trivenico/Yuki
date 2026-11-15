"""Validated, immutable specialist role registry.

Role configuration is trusted deployment configuration, never task/model input.
Action permission labels and executable tool names remain separate namespaces.
"""
from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
import re
from typing import Mapping

from automation.specialist_worker import build_delegated_specialist_handlers

_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$")
_TOKEN = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,99}$")
_BUDGET_CAPS = {
    "max_model_turns": 8,
    "max_tool_calls": 20,
    "max_duration_seconds": 120,
    "max_transcript_chars": 80_000,
}


@dataclass(frozen=True)
class SpecialistBudget:
    max_model_turns: int = 4
    max_tool_calls: int = 8
    max_duration_seconds: int = 45
    max_transcript_chars: int = 40_000

    def validate(self) -> None:
        for name, maximum in _BUDGET_CAPS.items():
            value = getattr(self, name)
            if type(value) is not int or not 1 <= value <= maximum:
                raise ValueError(f"{name} must be between 1 and {maximum}")


@dataclass(frozen=True)
class SpecialistDefinition:
    agent_id: str
    display_name: str
    role: str
    capabilities: tuple[str, ...]
    action: str
    action_permissions: tuple[str, ...] = ()
    allowed_tools: tuple[str, ...] = ()
    provider_preferences: tuple[str, ...] = ()
    budget: SpecialistBudget = SpecialistBudget()
    requires_approval: bool = True
    enabled: bool = True

    def validate(self) -> None:
        if not isinstance(self.agent_id, str) or not _ID.fullmatch(self.agent_id):
            raise ValueError("agent_id must be a bounded identifier")
        if not isinstance(self.display_name, str) or not self.display_name.strip() or len(self.display_name) > 120:
            raise ValueError("display_name must be non-empty and bounded")
        if not isinstance(self.role, str) or not _TOKEN.fullmatch(self.role):
            raise ValueError("role must be a bounded identifier")
        if not isinstance(self.action, str) or not _TOKEN.fullmatch(self.action):
            raise ValueError("action must be a bounded identifier")
        for name, values in (
            ("capabilities", self.capabilities),
            ("action_permissions", self.action_permissions),
            ("allowed_tools", self.allowed_tools),
            ("provider_preferences", self.provider_preferences),
        ):
            if not isinstance(values, tuple) or len(values) > 32:
                raise ValueError(f"{name} must be a bounded tuple")
            if any(not isinstance(value, str) or not _TOKEN.fullmatch(value) for value in values):
                raise ValueError(f"{name} contains an invalid identifier")
            if len(set(values)) != len(values):
                raise ValueError(f"{name} must not contain duplicates")
        if not self.capabilities:
            raise ValueError("each specialist must declare at least one capability")
        if type(self.requires_approval) is not bool or type(self.enabled) is not bool:
            raise ValueError("approval and enabled flags must be booleans")
        if not isinstance(self.budget, SpecialistBudget):
            raise TypeError("budget must be a SpecialistBudget")
        self.budget.validate()


class SpecialistRegistry:
    """Immutable validated registry for up to 100 specialist role definitions."""

    def __init__(self, definitions):
        if isinstance(definitions, (str, bytes)):
            raise TypeError("definitions must be an iterable of SpecialistDefinition")
        entries = tuple(definitions)
        if not entries or len(entries) > 100:
            raise ValueError("registry must contain between 1 and 100 specialists")
        registry = {}
        for definition in entries:
            if not isinstance(definition, SpecialistDefinition):
                raise TypeError("all entries must be SpecialistDefinition instances")
            definition.validate()
            if definition.agent_id in registry:
                raise ValueError("specialist IDs must be unique")
            registry[definition.agent_id] = definition
        self._definitions = MappingProxyType(registry)

    @property
    def definitions(self) -> Mapping[str, SpecialistDefinition]:
        return self._definitions

    def get(self, agent_id: str) -> SpecialistDefinition:
        try:
            return self._definitions[agent_id]
        except KeyError as exc:
            raise KeyError("unknown specialist") from exc

    def enabled(self) -> tuple[SpecialistDefinition, ...]:
        return tuple(item for item in self._definitions.values() if item.enabled)

    def coordinator_agents(self):
        """Adapt role metadata to the coordinator; permissions are action labels."""
        from automation.agent_coordinator import SpecialistAgent
        return tuple(
            SpecialistAgent(
                agent_id=item.agent_id,
                name=item.display_name,
                capabilities=frozenset(item.capabilities),
                action=item.action,
                permissions=frozenset(item.action_permissions),
                enabled=item.enabled,
                requires_approval=item.requires_approval,
            )
            for item in self._definitions.values()
        )

    def build_handlers(self, runtime_bindings):
        """Build worker handlers from trusted per-agent runtime dependencies.

        Every enabled specialist needs an explicit owner-bound parent policy,
        model, tool handlers, and schemas. Bindings cannot alter role metadata,
        permissions, allowlists, or budgets.
        """
        if not isinstance(runtime_bindings, Mapping):
            raise TypeError("runtime_bindings must be a mapping")
        expected = {item.agent_id for item in self.enabled()}
        if set(runtime_bindings) != expected:
            raise ValueError("runtime bindings must exactly match enabled specialists")
        if not expected:
            return {}
        configs = {}
        for item in self.enabled():
            binding = runtime_bindings[item.agent_id]
            if not isinstance(binding, Mapping):
                raise TypeError("each runtime binding must be a mapping")
            required = {"model", "tools", "tool_definitions", "parent_policy"}
            optional = {"stream_model", "audit", "provider_name"}
            if not required.issubset(binding) or set(binding).difference(required | optional):
                raise ValueError("runtime binding contains missing or unsupported fields")
            if item.provider_preferences:
                provider_name = binding.get("provider_name")
                if not isinstance(provider_name, str) or provider_name.strip().lower() not in item.provider_preferences:
                    raise ValueError("runtime provider is outside the specialist provider preferences")
            configs[item.agent_id] = {
                **{key: value for key, value in binding.items() if key != "provider_name"},
                "allowed_tools": item.allowed_tools,
                "loop_options": {
                    "max_model_turns": item.budget.max_model_turns,
                    "max_tool_calls": item.budget.max_tool_calls,
                    "max_duration_seconds": item.budget.max_duration_seconds,
                    "max_transcript_chars": item.budget.max_transcript_chars,
                },
            }
        return build_delegated_specialist_handlers(configs)
