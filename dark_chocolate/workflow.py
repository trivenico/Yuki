"""Bounded workflow lifecycle. Plans never execute by themselves."""
from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from uuid import uuid4

class WorkflowState(str, Enum):
    PLANNING = "PLANNING"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    EXECUTING = "EXECUTING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"

@dataclass(frozen=True)
class PlanStep:
    goal: str
    tool_name: str | None = None
    arguments: dict[str, object] = field(default_factory=dict)

@dataclass
class Workflow:
    request: str
    steps: list[PlanStep]
    workflow_id: str = field(default_factory=lambda: str(uuid4()))
    state: WorkflowState = WorkflowState.PLANNING
    def request_approval(self) -> None:
        if self.state is not WorkflowState.PLANNING:
            raise RuntimeError("workflow is not in planning state")
        self.state = WorkflowState.PENDING_APPROVAL
    def begin(self, approved: bool) -> None:
        if self.state is not WorkflowState.PENDING_APPROVAL or approved is not True:
            self.state = WorkflowState.FAILED
            raise PermissionError("explicit plan approval required")
        self.state = WorkflowState.EXECUTING
    def complete(self) -> None:
        if self.state is not WorkflowState.EXECUTING:
            raise RuntimeError("workflow is not executing")
        self.state = WorkflowState.COMPLETED

def make_plan(request: str) -> Workflow:
    if not isinstance(request, str) or not request.strip():
        raise ValueError("request cannot be empty")
    workflow = Workflow(request=request, steps=[PlanStep(goal=request.strip())])
    workflow.request_approval()
    return workflow
