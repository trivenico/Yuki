"""Bounded inspectable planner. It proposes work; it does not autonomously execute tools."""
from dataclasses import dataclass
from itertools import islice
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
    def __init__(self, *, max_steps=5, max_goal_chars=2000):
        if isinstance(max_steps, bool) or not isinstance(max_steps, int) or not 1 <= max_steps <= 10:
            raise ValueError("max_steps must be between 1 and 10")
        if (
            isinstance(max_goal_chars, bool)
            or not isinstance(max_goal_chars, int)
            or not 1 <= max_goal_chars <= 20_000
        ):
            raise ValueError("max_goal_chars must be between 1 and 20000")
        self.max_steps, self.max_goal_chars = max_steps, max_goal_chars

    def propose(self, goal: str, *, memory_context: Iterable[str] = ()) -> Plan:
        if not isinstance(goal, str) or not goal.strip() or len(goal) > self.max_goal_chars:
            raise ValueError("Goal must be non-empty and bounded.")

        # Consume at most ten context items. Materializing an arbitrary iterable
        # first could exhaust memory or hang forever on an unbounded generator.
        context = tuple(str(item)[:500] for item in islice(iter(memory_context), 10))
        steps = (
            PlanStep("understand", "Clarify acceptance criteria and constraints"),
            PlanStep("plan", "Propose the smallest reviewable sequence"),
            PlanStep("policy_check", "Check permissions, target scope, and workspace boundaries"),
            PlanStep("execute_one", "Perform one approved action at a time"),
            PlanStep("verify", "Inspect output and run relevant tests"),
            PlanStep("report", "Summarize verified outcomes and remaining risks", False),
        )
        return Plan(goal.strip(), steps[:self.max_steps], context)
