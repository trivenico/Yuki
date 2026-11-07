"""Trusted constructor for applying advisory reasoning budgets to agent execution.

The route planner is advisory. This adapter is the explicit hand-off point used
by an approved runtime to intersect that budget with the execution loop's
existing hard defaults. It does not grant tools or bypass authorization.
"""
from __future__ import annotations

from typing import Any, Mapping

from core.agent_execution_loop import AgentExecutionLoop

_HARD_DEFAULTS = {
    "max_model_turns": 8,
    "max_tool_calls": 20,
    "max_duration_seconds": 120,
    "max_transcript_chars": 80_000,
}
_ALLOWED_METADATA = {"profile", "rationale"}


def build_budgeted_execution_loop(
    model,
    tool_protocol,
    reasoning_budget: Mapping[str, Any],
    **kwargs: Any,
) -> AgentExecutionLoop:
    """Build an execution loop whose limits cannot exceed policy or hard defaults.

    Call only after the parent task has been approved. The caller remains
    responsible for providing an already-authorized StructuredToolProtocol.
    Explicit per-runtime kwargs may further reduce limits, never increase them.
    """
    if not isinstance(reasoning_budget, Mapping):
        raise TypeError("reasoning_budget_must_be_mapping")
    required = set(_HARD_DEFAULTS)
    keys = set(reasoning_budget)
    if not required.issubset(keys) or keys - required - _ALLOWED_METADATA:
        raise ValueError("invalid_reasoning_budget_fields")

    effective = {}
    for name, hard_default in _HARD_DEFAULTS.items():
        value = reasoning_budget[name]
        if type(value) is not int or value < 1:
            raise ValueError("invalid_reasoning_budget:" + name)
        caller_limit = kwargs.pop(name, hard_default)
        if isinstance(caller_limit, bool) or not isinstance(caller_limit, (int, float)):
            raise ValueError("invalid_runtime_limit:" + name)
        if caller_limit < 1:
            raise ValueError("invalid_runtime_limit:" + name)
        effective[name] = min(value, hard_default, caller_limit)

    return AgentExecutionLoop(
        model,
        tool_protocol,
        **effective,
        **kwargs,
    )
