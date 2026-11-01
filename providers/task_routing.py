"""Deterministic task-aware model routing with explicit privacy boundaries.

This module chooses among configured, ready adapters. It does not infer model
quality, cost, context size, or entitlements from model names. Those require
measured metadata and remain future extensions.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from providers.capabilities import CapabilityRegistry


_ALLOWED_TASKS = frozenset({
    "routine", "engineering", "complex_reasoning", "security", "long_horizon",
})
_ALLOWED_PRIVACY = frozenset({"local_only", "cloud_allowed"})
_ALLOWED_EFFORTS = frozenset({"low", "medium", "high", "xhigh", "max"})
_TASK_EFFORT = {
    "routine": "low",
    "engineering": "high",
    "complex_reasoning": "xhigh",
    "security": "high",
    "long_horizon": "high",
}
_TASK_ORDER = {
    "routine": ("ollama", "openai", "openai_responses", "anthropic", "gemini"),
    "engineering": ("openai_responses", "anthropic", "ollama", "openai", "gemini"),
    "complex_reasoning": ("openai_responses", "anthropic", "openai", "ollama", "gemini"),
    "security": ("ollama", "openai_responses", "anthropic", "openai", "gemini"),
    "long_horizon": ("openai_responses", "anthropic", "ollama", "openai", "gemini"),
}


@dataclass(frozen=True)
class TaskProfile:
    task_type: str
    privacy: str = "local_only"
    required_capabilities: tuple[str, ...] = ("text",)
    preferred_providers: tuple[str, ...] = ()
    reasoning_effort: str | None = None
    require_human_review: bool = False

    def __post_init__(self):
        if self.task_type not in _ALLOWED_TASKS:
            raise ValueError("task_type must be one of: " + ", ".join(sorted(_ALLOWED_TASKS)))
        if self.privacy not in _ALLOWED_PRIVACY:
            raise ValueError("privacy must be local_only or cloud_allowed")
        if not isinstance(self.required_capabilities, tuple) or not self.required_capabilities:
            raise ValueError("required_capabilities must be a non-empty tuple")
        if len(self.required_capabilities) > 6 or any(not isinstance(x, str) or not x for x in self.required_capabilities):
            raise ValueError("required_capabilities must contain 1 to 6 capability names")
        if not isinstance(self.preferred_providers, tuple) or len(self.preferred_providers) > 32:
            raise ValueError("preferred_providers must be a tuple of at most 32 names")
        if any(not isinstance(x, str) or not x.strip() for x in self.preferred_providers):
            raise ValueError("preferred provider names must be non-empty strings")
        if self.reasoning_effort is not None and self.reasoning_effort not in _ALLOWED_EFFORTS:
            raise ValueError("reasoning_effort must be low, medium, high, xhigh, or max")
        if type(self.require_human_review) is not bool:
            raise TypeError("require_human_review must be a boolean")


@dataclass(frozen=True)
class TaskRoutingDecision:
    provider: str
    model: str | None
    task_type: str
    privacy: str
    reasoning_effort: str
    require_human_review: bool
    rationale: str


class TaskRoutingPolicy:
    """Explainable routing policy; cloud use is opt-in per task."""

    def route(
        self,
        registry: CapabilityRegistry,
        profile: TaskProfile,
    ) -> TaskRoutingDecision:
        if not isinstance(registry, CapabilityRegistry):
            raise TypeError("registry must be a CapabilityRegistry")
        if not isinstance(profile, TaskProfile):
            raise TypeError("profile must be a TaskProfile")
        preferences = profile.preferred_providers or _TASK_ORDER[profile.task_type]
        allow_cloud = profile.privacy == "cloud_allowed"
        selected = registry.route(
            required=profile.required_capabilities,
            preference=preferences,
            allow_cloud=allow_cloud,
            strict_preference=False,
        )
        effort = profile.reasoning_effort or _TASK_EFFORT[profile.task_type]
        rationale = (
            f"{profile.task_type} task; selected configured, ready {selected.privacy} "
            f"provider satisfying {', '.join(profile.required_capabilities)}; "
            f"cloud {'explicitly allowed' if allow_cloud else 'excluded'}."
        )
        return TaskRoutingDecision(
            provider=selected.provider,
            model=selected.configured_model,
            task_type=profile.task_type,
            privacy=profile.privacy,
            reasoning_effort=effort,
            require_human_review=(
                profile.require_human_review or profile.task_type == "security"
            ),
            rationale=rationale,
        )


def route_task(registry: CapabilityRegistry, profile: TaskProfile) -> TaskRoutingDecision:
    """Convenience function for callers that already own a capability registry."""
    return TaskRoutingPolicy().route(registry, profile)
