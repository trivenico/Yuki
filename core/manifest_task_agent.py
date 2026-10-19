"""LLM-assisted task proposal parser; model output proposes but never executes actions."""
from __future__ import annotations
from dataclasses import dataclass
import json
from typing import Any

@dataclass(frozen=True)
class ProposedAction:
    skill_id: str
    arguments: dict[str, Any]

@dataclass(frozen=True)
class TaskPlanResult:
    ok: bool
    status: str
    action: ProposedAction | None = None
    message: str = ""

class ManifestTaskAgent:
    def __init__(self, provider, agent, dispatcher):
        self.provider, self.agent, self.dispatcher = provider, agent, dispatcher

    def run(self, task: str) -> TaskPlanResult:
        if not isinstance(task, str) or not task.strip() or len(task) > 8000:
            return TaskPlanResult(False, "invalid_task", message="Task must be non-empty and bounded.")
        try:
            raw = self.provider.generate(task)
            value = json.loads(raw) if isinstance(raw, str) else None
        except Exception:
            return TaskPlanResult(False, "invalid_plan", message="Provider returned an invalid plan.")
        if not isinstance(value, dict) or value.get("type") != "tool_call":
            return TaskPlanResult(False, "no_action", message="No supported action was proposed.")
        skill_id, arguments = value.get("skill_id"), value.get("arguments")
        if not isinstance(skill_id, str) or not isinstance(arguments, dict):
            return TaskPlanResult(False, "invalid_plan", message="Proposed action is malformed.")
        if self.dispatcher.describe(skill_id) is None:
            return TaskPlanResult(False, "skill_not_registered", message="Proposed skill is not registered.")
        return TaskPlanResult(True, "approval_required", ProposedAction(skill_id, arguments),
                              "Action proposed; awaiting explicit approval.")
