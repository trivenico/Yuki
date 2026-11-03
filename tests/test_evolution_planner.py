import tempfile
import unittest
from pathlib import Path

from automation.agent_coordinator import SpecialistAgent, SpecialistAgentCoordinator, SubtaskSpec
from automation.evolution_planner import EvolutionPlanner, EvolutionProposal
from automation.task_runtime import TaskRuntime
from automation.workflow_store import WorkflowRunStore


class EvolutionPlannerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        db = str(Path(self.tmp.name) / "yuki.sqlite3")
        self.store = WorkflowRunStore(db)
        self.runtime = TaskRuntime(
            self.store,
            authorize=lambda actor, action, task: actor == "alice" and action in {
                "task:create", "task:read", "task:cancel"
            },
        )
        self.coordinator = SpecialistAgentCoordinator(
            self.runtime, db,
            [SpecialistAgent("research", "Research", frozenset({"research"}), "emit")],
            authorize=lambda actor, action, resource: actor == "alice" and action in {
                "agent:plan", "agent:dispatch", "agent:read"
            },
        )
        self.planner = EvolutionPlanner(self.coordinator)

    def proposal(self, request_id="upgrade-1", requires_approval=False):
        return EvolutionProposal(
            objective="Improve repository test reliability",
            request_id=request_id,
            subtasks=(SubtaskSpec(
                "inspect", "Inspect current test reliability", ("research",),
                requires_approval=requires_approval,
            ),),
        )

    def test_plan_persists_approval_required_without_dispatch(self):
        result = self.planner.plan(actor_id="alice", proposal=self.proposal())
        self.assertTrue(result.approval_required)
        self.assertFalse(result.dispatch_started)
        self.assertTrue(result.plan.subtasks[0].requires_approval)
        self.assertIsNone(result.plan.subtasks[0].task_id)
        self.assertEqual(result.plan.subtasks[0].status, "pending")

    def test_same_request_is_idempotent(self):
        first = self.planner.plan(actor_id="alice", proposal=self.proposal())
        second = self.planner.plan(actor_id="alice", proposal=self.proposal())
        self.assertEqual(first.plan.plan_id, second.plan.plan_id)

    def test_changed_objective_gets_distinct_plan(self):
        first = self.planner.plan(actor_id="alice", proposal=self.proposal())
        changed = EvolutionProposal(
            objective="Improve dependency security",
            request_id="upgrade-1",
            subtasks=self.proposal().subtasks,
        )
        second = self.planner.plan(actor_id="alice", proposal=changed)
        self.assertNotEqual(first.plan.plan_id, second.plan.plan_id)
        self.assertNotEqual(first.objective_digest, second.objective_digest)

    def test_unauthorized_actor_cannot_create_evolution_plan(self):
        with self.assertRaises(PermissionError):
            self.planner.plan(actor_id="mallory", proposal=self.proposal())

    def test_invalid_objective_and_request_id_are_rejected(self):
        with self.assertRaises(ValueError):
            self.planner.plan(actor_id="alice", proposal=EvolutionProposal(
                objective="  ", request_id="bad", subtasks=self.proposal().subtasks,
            ))
        with self.assertRaises(ValueError):
            self.planner.plan(actor_id="alice", proposal=EvolutionProposal(
                objective="valid", request_id="../unsafe", subtasks=self.proposal().subtasks,
            ))

    def test_invalid_or_empty_subtasks_are_rejected(self):
        with self.assertRaises(ValueError):
            self.planner.plan(actor_id="alice", proposal=EvolutionProposal(
                objective="valid", request_id="empty", subtasks=(),
            ))


if __name__ == "__main__":
    unittest.main()
