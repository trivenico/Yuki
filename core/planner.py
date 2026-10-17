"""Bounded, deterministic planner foundation with memory context.

This is not an LLM reasoning engine. It produces inspectable proposals and never
executes tools. A model-driven loop must call the same policy gate before execution.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class PlanStep:
    action: str
    description: str
    requires_approval: bool = True


@dataclass(frozen=True)
class Plan:
    goal: str
    steps: tuple[PlanStep, ...]
    memory_context: tuple[str, ...] = ()


class Planner:
    def __init__(self, *, max_steps: int = 5, max_goal_chars: int = 2000):
        if isinstance(max_steps, bool) or not isinstance(max_steps, int) or not 1 <= max_steps <= 10:
            raise ValueError("max_steps must be between 1 and 10")
        self.max_steps, self.max_goal_chars = max_steps, max_goal_chars

    def propose(self, goal: str, *, memory_context: Iterable[str] = ()) -> Plan:
        if not isinstance(goal, str) or not goal.strip() or len(goal) > self.max_goal_chars:
            raise ValueError("Goal must be non-empty and bounded.")
        context = tuple(str(item)[:500] for item in list(memory_context)[:10])
        cleaned = goal.strip()
        steps = (
            PlanStep("understand", f"Clarify expected outcome for: {cleaned}"),
            PlanStep("plan", "Propose a minimal, reviewable sequence of changes"),
            PlanStep("policy_check", "Check scope, permissions, and workspace boundaries"),
            PlanStep("execute", "Execute one approved action at a time"),
            PlanStep("verify", "Run relevant checks and report evidence"),
        )[:self.max_steps]
        return Plan(cleaned, steps, context)
