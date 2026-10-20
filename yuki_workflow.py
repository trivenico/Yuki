"""Typed lifecycle primitives for Yuki's perception-to-report orchestration.

This module models state transitions only. It deliberately does not invoke
providers, tools, operating-system commands, networks, or approval decisions.
Execution integration must re-check policy and authorization at every step.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from uuid import uuid4

MAX_GOAL_LENGTH = 500


class Stage(str, Enum):
    WAKE = "wake"
    PERCEIVE = "perceive"
    UNDERSTAND = "understand_intent"
    PLAN = "plan"
    CHECK_TOOLS = "check_tools"
    ASK_PERMISSION = "ask_permission"
    EXECUTE = "execute"
    OBSERVE = "observe_output"
    FEEDBACK = "feedback"
    REPORT = "report"
    COMPLETE = "complete"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"
    FAILED = "failed"


@dataclass
class Workflow:
    id: str
    goal: str
    stage: Stage = Stage.WAKE
    history: list[dict] = field(default_factory=list)
    created_at: str = ""
    failure_reason: str = ""


_ALLOWED_TRANSITIONS = {
    Stage.WAKE: {Stage.PERCEIVE, Stage.CANCELLED},
    Stage.PERCEIVE: {Stage.UNDERSTAND, Stage.CANCELLED, Stage.FAILED},
    Stage.UNDERSTAND: {Stage.PLAN, Stage.REPORT, Stage.CANCELLED, Stage.FAILED},
    Stage.PLAN: {Stage.CHECK_TOOLS, Stage.REPORT, Stage.CANCELLED, Stage.FAILED},
    Stage.CHECK_TOOLS: {
        Stage.ASK_PERMISSION,
        Stage.EXECUTE,
        Stage.BLOCKED,
        Stage.CANCELLED,
        Stage.FAILED,
    },
    Stage.ASK_PERMISSION: {Stage.EXECUTE, Stage.BLOCKED, Stage.CANCELLED},
    Stage.EXECUTE: {Stage.OBSERVE, Stage.BLOCKED, Stage.CANCELLED, Stage.FAILED},
    Stage.OBSERVE: {Stage.FEEDBACK, Stage.FAILED, Stage.CANCELLED},
    Stage.FEEDBACK: {Stage.PLAN, Stage.REPORT, Stage.CANCELLED, Stage.FAILED},
    Stage.REPORT: {Stage.COMPLETE, Stage.FAILED},
    Stage.BLOCKED: {Stage.REPORT, Stage.CANCELLED},
    Stage.FAILED: {Stage.REPORT},
    Stage.COMPLETE: set(),
    Stage.CANCELLED: set(),
}


def create_workflow(goal):
    if not isinstance(goal, str) or not goal.strip():
        raise ValueError("Workflow goal is required.")
    now = datetime.now(timezone.utc).isoformat()
    workflow = Workflow(
        id=uuid4().hex[:12],
        goal=goal.strip()[:MAX_GOAL_LENGTH],
        created_at=now,
    )
    workflow.history.append({"stage": Stage.WAKE.value, "at": now, "note": "Workflow created."})
    return workflow


def transition(workflow, next_stage, note=""):
    """Apply one legal state transition; callers must enforce real policy separately."""
    if not isinstance(workflow, Workflow):
        raise TypeError("workflow must be a Workflow.")
    try:
        next_stage = next_stage if isinstance(next_stage, Stage) else Stage(next_stage)
    except (ValueError, TypeError):
        raise ValueError("Unknown workflow stage.") from None
    if next_stage not in _ALLOWED_TRANSITIONS[workflow.stage]:
        raise ValueError(
            f"Illegal workflow transition: {workflow.stage.value} -> {next_stage.value}"
        )
    now = datetime.now(timezone.utc).isoformat()
    workflow.stage = next_stage
    safe_note = str(note or "")[:300]
    workflow.history.append({"stage": next_stage.value, "at": now, "note": safe_note})
    return workflow


def workflow_summary(workflow):
    if not isinstance(workflow, Workflow):
        raise TypeError("workflow must be a Workflow.")
    lines = [f"Workflow {workflow.id}: {workflow.goal}", f"Stage: {workflow.stage.value}"]
    lines.extend(f"- {event['stage']}: {event['note']}" for event in workflow.history)
    lines.append("State tracking only; no tools or external actions have been executed.")
    return "\n".join(lines)
