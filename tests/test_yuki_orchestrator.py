import unittest

from yuki_approvals import ApprovalGate
from yuki_events import EventBus
from yuki_orchestrator import TaskOrchestrator
from yuki_security import SecurityPolicy
from yuki_workflow import Stage


class OrchestratorTests(unittest.TestCase):
    def setUp(self):
        self.events = EventBus()
        self.received = []
        self.events.subscribe("task.prepared", self.received.append)
        self.orchestrator = TaskOrchestrator(
            policy=SecurityPolicy(allowed_tools=["calculate", "list_files"]),
            approvals=ApprovalGate(),
            events=self.events,
        )

    def test_safe_task_is_prepared_but_not_executed(self):
        result = self.orchestrator.prepare(
            "Calculate a result",
            ["Check input", "Calculate", "Explain result"],
            required_tools=["calculate"],
        )
        self.assertEqual(result.status, "prepared")
        self.assertEqual(result.workflow.stage, Stage.CHECK_TOOLS)
        self.assertIn("no tools have executed", result.message)
        self.assertEqual(len(self.received), 1)
        self.assertTrue(all(step.status == "pending" for step in result.plan.steps))

    def test_disallowed_tool_blocks_task(self):
        result = self.orchestrator.prepare(
            "Run shell command", ["Inspect request"], required_tools=["shell"]
        )
        self.assertEqual(result.status, "blocked")
        self.assertEqual(result.workflow.stage, Stage.BLOCKED)
        self.assertIn("shell", result.message)

    def test_approval_is_required_and_decision_does_not_execute(self):
        result = self.orchestrator.prepare(
            "Run an active test in my lab",
            ["Confirm lab scope", "Run approved test"],
            required_tools=["calculate"],
            requires_approval=True,
            rationale="User must confirm the target is their authorised lab.",
        )
        self.assertEqual(result.status, "awaiting_approval")
        self.assertEqual(result.workflow.stage, Stage.ASK_PERMISSION)
        message = self.orchestrator.decide_approval(result.workflow.id, True)
        self.assertIn("execution has not started", message)
        self.assertEqual(result.status, "approved_for_next_step")
        self.assertEqual(result.workflow.stage, Stage.ASK_PERMISSION)

    def test_rejected_approval_blocks_task(self):
        result = self.orchestrator.prepare(
            "Change lab configuration",
            ["Review change"],
            requires_approval=True,
            rationale="Configuration changes require explicit consent.",
        )
        message = self.orchestrator.decide_approval(result.workflow.id, False)
        self.assertIn("Approval rejected", message)
        self.assertEqual(result.status, "blocked")
        self.assertEqual(result.workflow.stage, Stage.BLOCKED)

    def test_missing_rationale_is_rejected(self):
        with self.assertRaises(ValueError):
            self.orchestrator.prepare("Consequential task", ["Review"], requires_approval=True)

    def test_invalid_and_duplicate_tool_names_are_blocked(self):
        invalid = self.orchestrator.prepare(
            "Invalid tool", ["Review"], required_tools=["calculate", ""]
        )
        self.assertEqual(invalid.status, "blocked")
        duplicate = self.orchestrator.prepare(
            "Duplicate tool", ["Review"], required_tools=["calculate", "calculate"]
        )
        self.assertEqual(duplicate.status, "blocked")

    def test_unhashable_tool_name_is_blocked_without_crashing(self):
        result = self.orchestrator.prepare(
            "Malformed tool", ["Review"], required_tools=[["calculate"]]
        )
        self.assertEqual(result.status, "blocked")
        self.assertEqual(result.workflow.stage, Stage.BLOCKED)

    def test_required_tool_limit_is_enforced(self):
        with self.assertRaises(ValueError):
            self.orchestrator.prepare("Too many", ["Review"], required_tools=["x"] * 11)

    def test_unknown_task_cannot_be_approved(self):
        self.assertEqual(self.orchestrator.decide_approval("missing", True), "Task not found.")


if __name__ == "__main__":
    unittest.main()
