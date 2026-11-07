import tempfile
import unittest
from pathlib import Path

from automation.agent_budget_lifecycle import (
    AgentBudgetAllocationError,
    AgentBudgetLifecycle,
)
from core.reasoning.execution_budget_store import ExecutionBudgetStore


LIMITS = {
    "max_model_turns": 10,
    "max_tool_calls": 10,
    "max_duration_ms": 60_000,
    "max_transcript_chars": 10_000,
}


class AgentBudgetLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = ExecutionBudgetStore(str(Path(self.tmp.name) / "budgets.sqlite3"))
        self.lifecycle = AgentBudgetLifecycle(self.store)

    def test_plan_snapshot_retry_verifies_existing_snapshot(self):
        first = self.lifecycle.ensure_plan_budget(
            plan_id="plan-123", limits=LIMITS, policy_version="v1",
        )
        restarted = AgentBudgetLifecycle(
            ExecutionBudgetStore(str(Path(self.tmp.name) / "budgets.sqlite3"))
        )
        second = restarted.ensure_plan_budget(
            plan_id="plan-123", limits=LIMITS, policy_version="v1",
        )
        self.assertEqual(first, second)

    def test_plan_retry_rejects_policy_or_limit_drift(self):
        self.lifecycle.ensure_plan_budget(
            plan_id="plan-123", limits=LIMITS, policy_version="v1",
        )
        changed = dict(LIMITS)
        changed["max_model_turns"] = 9
        with self.assertRaises(AgentBudgetAllocationError):
            self.lifecycle.ensure_plan_budget(
                plan_id="plan-123", limits=changed, policy_version="v1",
            )

    def test_subtask_allocation_is_idempotent_across_restart(self):
        parent = self.lifecycle.ensure_plan_budget(
            plan_id="plan-123", limits=LIMITS, policy_version="v1",
        )
        child_limits = {
            "max_model_turns": 3,
            "max_tool_calls": 3,
            "max_duration_ms": 10_000,
            "max_transcript_chars": 2_000,
        }
        first = self.lifecycle.ensure_subtask_budget(
            plan_id="plan-123", subtask_id="research",
            parent_budget_id=parent.budget_id, limits=child_limits,
            policy_version="v1",
        )
        restarted = AgentBudgetLifecycle(
            ExecutionBudgetStore(str(Path(self.tmp.name) / "budgets.sqlite3"))
        )
        second = restarted.ensure_subtask_budget(
            plan_id="plan-123", subtask_id="research",
            parent_budget_id=parent.budget_id, limits=child_limits,
            policy_version="v1",
        )
        self.assertEqual(first, second)

    def test_subtask_allocation_rejects_parent_capacity_overspend(self):
        parent = self.lifecycle.ensure_plan_budget(
            plan_id="plan-123", limits=LIMITS, policy_version="v1",
        )
        excessive = dict(LIMITS)
        excessive["max_model_turns"] = 11
        with self.assertRaises(AgentBudgetAllocationError):
            self.lifecycle.ensure_subtask_budget(
                plan_id="plan-123", subtask_id="oversized",
                parent_budget_id=parent.budget_id, limits=excessive,
                policy_version="v1",
            )


if __name__ == "__main__":
    unittest.main()
