import tempfile
import unittest
from pathlib import Path

from automation.agent_coordinator import SpecialistAgent, SpecialistAgentCoordinator
from automation.specialist_worker import SpecialistTaskWorker
from automation.task_runtime import TaskRuntime
from automation.workflow_approval import AuthenticatedPrincipal, WorkflowApprovalService
from automation.workflow_store import WorkflowRunStore
from automation.worker_supervisor import WorkerSupervisor
from core.agent_execution_service import AgentExecutionService
from core.model_agent_planner import ModelAgentPlanner


class FakeProvider:
    def __init__(self):
        self.calls = 0

    def generate(self, _prompt):
        self.calls += 1
        return (
            '{"subtasks":[{"subtask_id":"research","description":"Summarize supplied material",'
            '"required_capabilities":["research"],"depends_on":[],'
            '"required_permissions":["material:read"],"requires_approval":true}]}'
        )


class AgentExecutionServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        db = str(Path(self.temp.name) / "yuki.sqlite3")
        self.store = WorkflowRunStore(db)
        grants = {
            "task:create", "task:read", "task:progress", "task:artifact",
            "task:complete", "task:lease:renew", "task:recover", "task:fail",
            "task:cancel",
        }
        self.runtime = TaskRuntime(
            self.store,
            authorize=lambda actor, action, _task: (
                actor == "alice" and action in {"task:create", "task:read"}
            ) or (actor == "worker-service" and action in grants),
        )
        self.coordinator = SpecialistAgentCoordinator(
            self.runtime, db,
            [SpecialistAgent(
                "researcher", "Research specialist", frozenset({"research"}),
                "emit", frozenset({"material:read"}),
            )],
            authorize=lambda actor, action, _plan: actor == "alice" and action in {
                "agent:plan", "agent:dispatch", "agent:read"
            },
        )
        self.provider = FakeProvider()
        planner = ModelAgentPlanner(self.provider, self.coordinator)
        self.approvals = WorkflowApprovalService(
            self.store,
            principal_resolver=lambda context: {
                "reviewer-session": AuthenticatedPrincipal(
                    "reviewer", "trusted-session",
                    frozenset({"workflow:approve"}),
                )
            }[context],
        )
        worker = SpecialistTaskWorker(
            self.runtime,
            worker_id="specialist-worker",
            service_actor_id="worker-service",
            handlers={"researcher": lambda material: "Reviewed: " + material},
        )
        supervisor = WorkerSupervisor(self.runtime, worker, max_tasks_per_cycle=5)
        self.service = AgentExecutionService(
            planner, self.coordinator, self.approvals, supervisor,
        )

    def test_goal_to_approval_to_worker_completion(self):
        plan = self.service.submit_goal(
            actor_id="alice",
            idempotency_key="goal-1",
            goal="Research the supplied material",
        )
        self.assertEqual(len(plan.subtasks), 1)
        subtask = plan.subtasks[0]
        self.assertEqual(subtask.agent_id, "researcher")
        self.assertTrue(subtask.requires_approval)
        self.assertIsNotNone(subtask.task_id)
        self.assertEqual(subtask.status, "queued")
        self.assertEqual(self.provider.calls, 1)

        blocked = self.service.run_worker_cycle()
        self.assertEqual(blocked.processed_count, 0)
        self.assertTrue(blocked.idle)
        self.assertEqual(self.store.get(subtask.task_id).state, "queued")

        task = self.store.get(subtask.task_id)
        approved_plan = self.service.approve_subtask(
            actor_id="alice",
            plan_id=plan.plan_id,
            subtask_id="research",
            request_context="reviewer-session",
            expected_task_version=task.version,
        )
        self.assertEqual(approved_plan.subtasks[0].status, "queued")
        self.assertIsNotNone(self.store.get(subtask.task_id).approved_by)

        report = self.service.run_worker_cycle()
        self.assertEqual(report.processed_count, 1)
        self.assertEqual(report.succeeded_count, 1)
        completed = self.service.get_plan(actor_id="alice", plan_id=plan.plan_id)
        self.assertEqual(completed.subtasks[0].status, "succeeded")
        task_result = self.store.get(subtask.task_id)
        self.assertEqual(task_result.result["output"], "Reviewed: Summarize supplied material")

    def test_untrusted_actor_cannot_submit_or_read_plans(self):
        with self.assertRaises(PermissionError):
            self.service.submit_goal(
                actor_id="mallory", idempotency_key="forged", goal="Do work"
            )

    def test_service_rejects_mismatched_runtime_wiring(self):
        other_store = WorkflowRunStore(str(Path(self.temp.name) / "other.sqlite3"))
        other_runtime = TaskRuntime(
            other_store, authorize=lambda *_: True,
        )
        worker = SpecialistTaskWorker(
            other_runtime, worker_id="other", service_actor_id="worker-service",
            handlers={"researcher": lambda _: "x"},
        )
        supervisor = WorkerSupervisor(other_runtime, worker)
        with self.assertRaisesRegex(ValueError, "same TaskRuntime"):
            AgentExecutionService(
                self.service.planner, self.coordinator, self.approvals, supervisor,
            )


if __name__ == "__main__":
    unittest.main()
