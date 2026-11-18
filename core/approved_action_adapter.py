"""Strict bridge from confirmed action snapshots to registered Yuki skills.

Action IDs are explicitly bound to a registered skill and a caller-supplied,
reviewed argument decoder. This adapter never resolves methods from a planner.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping


@dataclass(frozen=True)
class ApprovedActionBinding:
    """Configuration for one action ID supported by the approval coordinator."""

    skill_name: str
    decode_arguments: Callable[[dict], str]


class ApprovedActionAdapter:
    """Execute only configured actions through YukiCoreAgent's normal gate.

    The adapter captures the registered skill object at construction and checks
    its identity again immediately before dispatch. SkillRegistry does not
    support replacement; YukiCoreAgent.run_skill then resolves the same member
    again and applies its fresh PermissionGate and one-shot invocation guard.
    """

    def __init__(self, agent: Any, bindings: Mapping[str, ApprovedActionBinding]):
        registry = getattr(agent, "registry", None)
        if not callable(getattr(registry, "get", None)):
            raise TypeError("agent must expose a registered skill registry.")
        if not isinstance(bindings, Mapping) or not bindings:
            raise ValueError("At least one explicit action binding is required.")

        resolved = {}
        for action_id, binding in bindings.items():
            if (not isinstance(action_id, str) or not action_id.strip()
                    or len(action_id) > 120):
                raise ValueError("Action IDs must be non-empty bounded strings.")
            if not isinstance(binding, ApprovedActionBinding):
                raise TypeError("Each action binding must be an ApprovedActionBinding.")
            if (not isinstance(binding.skill_name, str) or not binding.skill_name.strip()
                    or len(binding.skill_name) > 128):
                raise ValueError("A bounded registered skill name is required.")
            if not callable(binding.decode_arguments):
                raise TypeError("Every action binding requires an argument decoder.")
            skill = registry.get(binding.skill_name)
            if skill is None:
                raise ValueError(f"Configured skill is not registered: {binding.skill_name}")
            risk = getattr(skill, "risk", None)
            if risk not in {"low", "medium", "high"}:
                raise ValueError("Configured skill has an unsupported or critical risk level.")
            resolved[action_id] = (binding, skill)

        self._agent = agent
        self._registry = registry
        self._bindings = resolved

    def execute(self, action: dict):
        """Validate the exact action envelope and delegate through run_skill()."""
        if not isinstance(action, dict) or set(action) != {"skill_id", "arguments"}:
            raise ValueError("Approved action must contain only skill_id and arguments.")
        action_id = action.get("skill_id")
        arguments = action.get("arguments")
        if not isinstance(action_id, str) or not isinstance(arguments, dict):
            raise ValueError("Approved action has an invalid skill ID or arguments.")
        configured = self._bindings.get(action_id)
        if configured is None:
            raise ValueError("Approved action ID is not explicitly configured.")

        binding, expected_skill = configured
        # The agent's registry attribute is mutable even though individual registry
        # entries cannot be replaced. Refuse a swapped registry before run_skill().
        if getattr(self._agent, "registry", None) is not self._registry:
            raise RuntimeError("Agent skill registry changed; execution was blocked.")
        current_skill = self._registry.get(binding.skill_name)
        if current_skill is not expected_skill:
            raise RuntimeError("Registered skill identity changed; execution was blocked.")
        if getattr(current_skill, "risk", None) not in {"low", "medium", "high"}:
            raise RuntimeError("Registered skill risk is no longer supported.")

        argument = binding.decode_arguments(dict(arguments))
        if not isinstance(argument, str) or len(argument) > 20000:
            raise ValueError("Decoded skill arguments must be text no longer than 20000 characters.")
        try:
            argument.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise ValueError("Decoded skill arguments must be valid UTF-8.") from exc

        # run_skill performs a fresh PermissionGate request and the guarded,
        # one-shot registered-skill invocation. Never call skill.execute here.
        return self._agent.run_skill(binding.skill_name, argument)
