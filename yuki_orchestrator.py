"""Guarded task preparation that connects plans, workflows, policy, approvals, and events.

This module prepares and checks tasks only. It intentionally does not execute tools,
run shell commands, access the network, or claim that an action has completed.
"""
from dataclasses import dataclass
from typing import Optional

from yuki_approvals import ApprovalGate
from yuki_events import EventBus
from yuki_planner import TaskPlan, create_plan
from yuki_security import SecurityPolicy
from yuki_workflow import Stage, Workflow, create_workflow, transition

MAX_REQUIRED_TOOLS = 10


@dataclass
class PreparedTask:
    workflow: Workflow
    plan: TaskPlan
    status: str
    approval_request_id: Optional[str] = None
    message: str = ""


class TaskOrchestrator:
    """Create inspectable task plans and enforce tool/approval boundaries."""

    def __init__(self, policy=None, approvals=None, events=None):
        self.policy = policy if policy is not None else SecurityPolicy()
        self.approvals = approvals if approvals is not None else ApprovalGate()
        self.events = events if events is not None else EventBus()
        self._tasks = {}

    def prepare(self, goal, steps, required_tools=(), requires_approval=False, rationale=""):
        if not isinstance(required_tools, (list, tuple, set)):
            raise ValueError("required_tools must be a list, tuple, or set.")
        if len(required_tools) > MAX_REQUIRED_TOOLS:
            raise ValueError(f"At most {MAX_REQUIRED_TOOLS} required tools may be checked.")
        if not isinstance(requires_approval, bool):
            raise ValueError("requires_approval must be a boolean.")

        workflow = create_workflow(goal)
        plan = create_plan(goal, steps)
        for stage in (Stage.PERCEIVE, Stage.UNDERSTAND, Stage.PLAN, Stage.CHECK_TOOLS):
            transition(workflow, stage)
        self.events.publish("task.prepared", {"workflow_id": workflow.id, "plan_id": plan.id})

        requested = sorted({name for name in required_tools if isinstance(name, str)})
        invalid = len(requested) != len(set(required_tools)) if not isinstance(required_tools, set) else False
        if invalid or len(requested) != len(required_tools):
            transition(workflow, Stage.BLOCKED, "Invalid or duplicate tool names.")
            result = PreparedTask(workflow, plan, "blocked", message="Invalid or duplicate tool names.")
            self._tasks[workflow.id] = result
            self.events.publish("task.blocked", {"workflow_id": workflow.id, "reason": "invalid_tool_names"})
            return result

        denied = [name for name in requested if not self.policy.authorize_tool(name)]
        if denied:
            reason = "Required tools are not permitted: " + ", ".join(denied)
            transition(workflow, Stage.BLOCKED, reason)
            result = PreparedTask(workflow, plan, "blocked", message=reason)
            self._tasks[workflow.id] = result
            self.events.publish("task.blocked", {"workflow_id": workflow.id, "reason": "tool_policy"})
            return result

        if requires_approval:
            if not isinstance(rationale, str) or not rationale.strip():
                raise ValueError("A rationale is required when approval is needed.")
            request = self.approvals.request(goal, rationale)
            transition(workflow, Stage.ASK_PERMISSION, "Waiting for explicit human approval.")
            result = PreparedTask(
                workflow, plan, "awaiting_approval", approval_request_id=request.id,
                message="Plan prepared; no tools have executed. Awaiting human approval.",
            )
            self._tasks[workflow.id] = result
            self.events.publish("task.approval_requested", {
                "workflow_id": workflow.id, "approval_request_id": request.id
            })
            return result

        result = PreparedTask(
            workflow, plan, "prepared",
            message="Plan and tool policy checks completed; no tools have executed.",
        )
        self._tasks[workflow.id] = result
        self.events.publish("task.ready_for_review", {"workflow_id": workflow.id})
        return result

    def decide_approval(self, workflow_id, approved):
        """Record a human decision; never starts execution as a side effect."""
        result = self._tasks.get(workflow_id)
        if result is None:
            return "Task not found."
        if result.status != "awaiting_approval" or not result.approval_request_id:
            return "Task is not awaiting approval."
        decision = self.approvals.decide(result.approval_request_id, approved)
        if decision.endswith("approved. No action was executed."):
            result.status = "approved_for_next_step"
            result.message = "Approved for the next step; execution has not started."
            self.events.publish("task.approved", {"workflow_id": workflow_id})
        elif decision.endswith("rejected. No action was executed."):
            transition(result.workflow, Stage.BLOCKED, "Human approval was rejected.")
            result.status = "blocked"
            result.message = "Approval rejected; no tools have executed."
            self.events.publish("task.blocked", {"workflow_id": workflow_id, "reason": "approval_rejected"})
        else:
            return decision
        return result.message

    def get_task(self, workflow_id):
        return self._tasks.get(workflow_id)
