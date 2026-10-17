"""Bounded, inspectable planning primitives; plans do not execute themselves."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import uuid4

MAX_PLAN_STEPS = 10
MAX_TEXT_LENGTH = 500


@dataclass
class PlanStep:
    id: int
    description: str
    status: str = "pending"


@dataclass
class TaskPlan:
    id: str
    goal: str
    steps: list[PlanStep] = field(default_factory=list)
    status: str = "draft"
    created_at: str = ""


def create_plan(goal, steps):
    """Create a bounded plan from explicitly supplied steps; never runs tools."""
    if not isinstance(goal, str) or not goal.strip():
        raise ValueError("A plan goal is required.")
    if not isinstance(steps, (list, tuple)) or not steps:
        raise ValueError("Provide at least one explicit plan step.")
    if len(steps) > MAX_PLAN_STEPS:
        raise ValueError(f"A plan may contain at most {MAX_PLAN_STEPS} steps.")
    normalized = []
    for step in steps:
        if not isinstance(step, str) or not step.strip():
            raise ValueError("Every plan step must be non-empty text.")
        normalized.append(PlanStep(id=len(normalized) + 1, description=step.strip()[:MAX_TEXT_LENGTH]))
    return TaskPlan(
        id=uuid4().hex[:12],
        goal=goal.strip()[:MAX_TEXT_LENGTH],
        steps=normalized,
        status="draft",
        created_at=datetime.now(timezone.utc).isoformat(),
    )


def plan_summary(plan):
    if not isinstance(plan, TaskPlan):
        raise TypeError("plan must be a TaskPlan.")
    lines = [f"Plan {plan.id}: {plan.goal}", f"Status: {plan.status}"]
    lines.extend(f'{step.id}. [{step.status}] {step.description}' for step in plan.steps)
    lines.append("Plan is a proposal only; no steps have been executed.")
    return "\n".join(lines)
