import tempfile
import unittest
from pathlib import Path

from automation.agent_coordinator import SpecialistAgent, SpecialistAgentCoordinator
from automation.task_runtime import TaskRuntime
from automation.workflow_store import WorkflowRunStore
from core.model_agent_planner import ModelAgentPlanner


class FakeProvider:
    def __init__(self, response):
        self.response = response
        self.prompts = []
        self.calls = 0

    def generate(self, prompt):
        self.prompts.append(prompt)
        self.calls += 1
        return self.response


class ModelAgentPlannerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        db = str(Path(self.temp.name) / "yuki.sqlite3")
        store = WorkflowRunStore(db)
        grants = {"task:create", "task:read"}
        runtime = TaskRuntime(
            store,
            authorize=lambda actor, action, task: actor == "alice" and action in grants,
        )
        agents = [
            SpecialistAgent("research", "Research", frozenset({"research"}), "emit",
                            frozenset({"web:read"})),
            SpecialistAgent("writer", "Writer", frozenset({"write"}), "emit",
                            frozenset({"artifact:write"})),
        ]
        coordinator = SpecialistAgentCoordinator(
            runtime, db, agents,
            authorize=lambda actor, action, plan: actor == "alice" and action in {
                "agent:plan", "agent:dispatch", "agent:read"
            },
        )
        self.provider = FakeProvider(
            '{"subtasks":['
            '{"subtask_id":"collect","description":"Collect public evidence",'
            '"required_capabilities":["research"],"depends_on":[],'
            '"required_permissions":["web:read"],"requires_approval":true},'
            '{"subtask_id":"draft","description":"Draft a summary",'
            '"required_capabilities":["write"],"depends_on":["collect"],'
            '"required_permissions":["artifact:write"],"requires_approval":true}'
            ']}'
        )
        self.coordinator = coordinator
        self.planner = ModelAgentPlanner(self.provider, coordinator)

    def test_model_proposal_is_routed_and_dispatched_as_dependency_gated_tasks(self):
        plan = self.planner.plan(
            actor_id="alice", idempotency_key="conversation-turn-1",
            goal="Research the topic and prepare a concise report",
        )
        self.assertEqual(len(plan.subtasks), 2)
        by_id = {item.subtask_id: item for item in plan.subtasks}
        self.assertEqual(by_id["collect"].agent_id, "research")
        self.assertEqual(by_id["draft"].agent_id, "writer")
        self.assertIsNotNone(by_id["collect"].task_id)
        self.assertIsNone(by_id["draft"].task_id)
        self.assertEqual(by_id["collect"].status, "queued")
        task = self.coordinator.runtime.get(actor_id="alice", task_id=by_id["collect"].task_id)
        self.assertTrue(task.steps[0]["requires_approval"])
        self.assertTrue(self.coordinator.runtime.store._requires_approval(task.steps))
        self.assertIsNone(self.coordinator.runtime.store.claim_next(worker_id="worker-before-approval"))
        self.assertEqual(by_id["draft"].status, "pending")
        self.assertIn("untrusted", self.provider.prompts[0].lower())

    def test_model_output_is_strict_json_and_duplicate_keys_are_rejected(self):
        for response in (
            "markdown-wrapped JSON",
            '{"subtasks":[]}',
            '{"subtasks":[],"extra":true}',
            '{"subtasks":[],"subtasks":[]}',
            '{"subtasks":[{"subtask_id":"a"}]}',
        ):
            with self.subTest(response=response):
                with self.assertRaises(ValueError):
                    ModelAgentPlanner.parse_proposal(response)

    def test_invalid_graph_and_unknown_capability_fail_before_dispatch(self):
        self.provider.response = (
            '{"subtasks":['
            '{"subtask_id":"a","description":"A","required_capabilities":["missing"],'
            '"depends_on":[],"required_permissions":[],"requires_approval":true}]}'
        )
        with self.assertRaises(ValueError):
            self.planner.plan(
                actor_id="alice", idempotency_key="bad-plan-1", goal="Do a task"
            )

    def test_provider_is_not_called_for_invalid_goal(self):
        with self.assertRaises(ValueError):
            self.planner.plan(actor_id="alice", idempotency_key="bad-goal", goal=" ")
        self.assertEqual(self.provider.calls, 0)

    def test_untrusted_actor_cannot_submit_plan(self):
        with self.assertRaises(PermissionError):
            self.planner.plan(
                actor_id="mallory", idempotency_key="unauthorized", goal="Do a task"
            )


if __name__ == "__main__":
    unittest.main()
