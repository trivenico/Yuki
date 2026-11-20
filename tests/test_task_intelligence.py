import tempfile
import unittest
from pathlib import Path

from automation.agent_coordinator import SpecialistAgent, SpecialistAgentCoordinator, SubtaskSpec
from automation.task_intelligence import TaskRequest, prepare_task_plan, recommend_capabilities
from automation.task_runtime import TaskRuntime
from automation.workflow_store import WorkflowRunStore


class TaskIntelligenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        db = str(Path(self.tmp.name) / "yuki.sqlite3")
        store = WorkflowRunStore(db)
        runtime = TaskRuntime(
            store,
            authorize=lambda actor, action, task: actor == "owner",
        )
        def authorize(actor, action, resource):
            return actor == "owner" and action in {
                "agent:read", "agent:plan", "agent:dispatch",
            }
        self.coordinator = SpecialistAgentCoordinator(
            runtime, db,
            [
                SpecialistAgent(
                    "reviewer", "Code reviewer",
                    frozenset({"code-reviewer"}), "emit",
                ),
                SpecialistAgent(
                    "tester", "Test engineer",
                    frozenset({"test-engineer"}), "emit",
                ),
            ],
            authorize=authorize,
        )

    def test_recommendations_are_read_only_and_mark_availability(self):
        before = self.coordinator.list_agents(actor_id="owner")
        results = recommend_capabilities(
            coordinator=self.coordinator, actor_id="owner",
            goal="review code security", limit=10,
        )
        after = self.coordinator.list_agents(actor_id="owner")
        reviewer = next(item for item in results if item["capability_id"] == "code-reviewer")
        self.assertTrue(reviewer["available"])
        self.assertEqual(before, after)

    def test_prepare_persists_dag_without_dispatching(self):
        request = TaskRequest(
            goal="Review a change and validate it with tests",
            idempotency_key="review-and-test-001",
            subtasks=(
                SubtaskSpec(
                    "review", "Review the supplied code diff",
                    ("code-reviewer",), requires_approval=True,
                ),
                SubtaskSpec(
                    "tests", "Create and assess relevant tests",
                    ("test-engineer",), depends_on=("review",),
                    requires_approval=True,
                ),
            ),
        )
        plan = prepare_task_plan(
            coordinator=self.coordinator, actor_id="owner", request=request,
        )
        self.assertEqual(len(plan.subtasks), 2)
        self.assertEqual(plan.subtasks[1].depends_on, ("review",))
        self.assertIn("Overall goal: Review a change and validate it with tests", plan.subtasks[0].description)
        self.assertTrue(all(item.task_id is None for item in plan.subtasks))
        self.assertTrue(all(item.status == "pending" for item in plan.subtasks))

    def test_plan_submission_requires_owner_authorization(self):
        request = TaskRequest(
            goal="Review code",
            idempotency_key="unauthorized",
            subtasks=(SubtaskSpec("review", "Review code", ("code-reviewer",)),),
        )
        with self.assertRaises(PermissionError):
            prepare_task_plan(
                coordinator=self.coordinator, actor_id="intruder", request=request,
            )

    def test_unavailable_capability_fails_closed(self):
        request = TaskRequest(
            goal="Deploy application",
            idempotency_key="no-deployer",
            subtasks=(SubtaskSpec("deploy", "Deploy application", ("deployment-operator",)),),
        )
        from automation.agent_coordinator import AgentPlanError
        with self.assertRaises(AgentPlanError):
            prepare_task_plan(
                coordinator=self.coordinator, actor_id="owner", request=request,
            )

    def test_request_bounds_and_types(self):
        with self.assertRaises(TypeError):
            prepare_task_plan(coordinator=self.coordinator, actor_id="owner", request=object())
        with self.assertRaises(ValueError):
            prepare_task_plan(
                coordinator=self.coordinator, actor_id="owner",
                request=TaskRequest(" ", "empty", (SubtaskSpec("x", "x", ("code-reviewer",)),)),
            )
        with self.assertRaises(ValueError):
            prepare_task_plan(
                coordinator=self.coordinator, actor_id="owner",
                request=TaskRequest("goal", "empty-subtasks", ()),
            )


if __name__ == "__main__":
    unittest.main()
