"""Bounded, policy-owned reasoning effort profiles for agent execution.

Effort controls resource budgets; it does not grant tools, permissions, provider
access, or a promise of deeper reasoning quality. Callers must apply the returned
limits to an existing AgentExecutionLoop and retain its independent hard caps.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping


class ReasoningEffortError(ValueError):
    """Invalid effort selection or task classification."""


@dataclass(frozen=True)
class ReasoningBudget:
    profile: str
    max_model_turns: int
    max_tool_calls: int
    max_duration_seconds: int
    max_transcript_chars: int
    rationale: str

    def to_execution_options(self) -> dict[str, int]:
        return {
            "max_model_turns": self.max_model_turns,
            "max_tool_calls": self.max_tool_calls,
            "max_duration_seconds": self.max_duration_seconds,
            "max_transcript_chars": self.max_transcript_chars,
        }

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# Conservative defaults. They are workload ceilings, not model-token counts.
_PROFILES = {
    "low": ReasoningBudget(
        "low", 2, 2, 30, 12_000,
        "Short, low-risk requests; minimize latency and local resource use.",
    ),
    "standard": ReasoningBudget(
        "standard", 5, 8, 120, 40_000,
        "Normal multi-step assistance and bounded tool workflows.",
    ),
    "high": ReasoningBudget(
        "high", 10, 20, 300, 80_000,
        "Complex engineering, research and multi-stage analysis.",
    ),
    "maximum": ReasoningBudget(
        "maximum", 16, 32, 600, 100_000,
        "Explicitly authorized long-running work under strict global limits.",
    ),
}

_TASK_DEFAULTS = {
    "simple": "low",
    "conversation": "standard",
    "research": "high",
    "coding": "high",
    "engineering": "high",
    "safety_review": "maximum",
}


def resolve_reasoning_budget(
    *,
    task_class: str,
    requested_effort: str | None = None,
    device_limits: Mapping[str, int] | None = None,
) -> ReasoningBudget:
    """Resolve an allow-listed effort profile and intersect it with device limits.

    Device limits can only reduce resource ceilings. They cannot increase the
    policy maximum or introduce arbitrary execution options.
    """
    if not isinstance(task_class, str) or task_class not in _TASK_DEFAULTS:
        raise ReasoningEffortError("unsupported_task_class")
    effort = _TASK_DEFAULTS[task_class] if requested_effort is None else requested_effort
    if not isinstance(effort, str) or effort not in _PROFILES:
        raise ReasoningEffortError("unsupported_reasoning_effort")
    budget = _PROFILES[effort]
    limits = {} if device_limits is None else device_limits
    allowed = {
        "max_model_turns", "max_tool_calls",
        "max_duration_seconds", "max_transcript_chars",
    }
    if not isinstance(limits, Mapping) or set(limits) - allowed:
        raise ReasoningEffortError("unsupported_device_limit")
    values = budget.to_execution_options()
    for name, limit in limits.items():
        if type(limit) is not int or limit < 1:
            raise ReasoningEffortError("invalid_device_limit:" + name)
        values[name] = min(values[name], limit)
    return ReasoningBudget(
        profile=budget.profile,
        max_model_turns=values["max_model_turns"],
        max_tool_calls=values["max_tool_calls"],
        max_duration_seconds=values["max_duration_seconds"],
        max_transcript_chars=values["max_transcript_chars"],
        rationale=budget.rationale,
    )


def available_effort_profiles() -> tuple[str, ...]:
    """Return supported profile names in increasing resource order."""
    return tuple(_PROFILES)
