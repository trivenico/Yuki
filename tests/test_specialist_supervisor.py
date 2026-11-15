import tempfile
import unittest
from pathlib import Path

from automation.agent_coordinator import SpecialistAgent, SpecialistAgentCoordinator, SubtaskSpec
from automation.specialist_supervisor import SpecialistSupervisor
from automation.specialist_worker import SpecialistTaskWorker
from automation.task_runtime import TaskRuntime
from automation.workflow_store import WorkflowRunStore


class SpecialistSupervisorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        database = str(Path(self.tmp.name) / "yuki.sqlite3")
        self.store = WorkflowRunStore(database)
        task_actions = {
            "task:create", "task:read", "task:progress", "task:complete",
            "task:fail", "task:cancel", "task:artifact",
        }
        self.runtime = TaskRuntime(
            self.store,
            authorize=lambda actor, action, _task: (
                actor in {"alice", "worker-service"} and action in task_actions
            ),
        )
        self.coordinator = SpecialistAgentCoordinator(
            self.runtime,
            database,
            [
                SpecialistAgent("research", "Research specialist", frozenset({"research"}), "emit"),
                SpecialistAgent("review", "Review specialist", frozenset({"review"}), "emit"),
            ],
            authorize=lambda actor, action, _plan: (
                actor == "alice" and action in {"agent:plan", "agent:dispatch", "agent:read"}
            ),
        )
        self.worker = SpecialistTaskWorker(
            self.runtime,
            worker_id="supervisor-worker",
            service_actor_id="worker-service",
            handlers={
                "research": lambda prompt: "facts for " + prompt,
                "review": lambda prompt: "reviewed " + prompt,
            },
            agent_ids=("research", "review"),
        )
        self.supervisor = SpecialistSupervisor(self.coordinator, self.worker)

    def test_runs_dependency_graph_and_validates_final_outputs(self):
        report = self.supervisor.run(
            actor_id="alice",
            idempotency_key="supervisor-dag",
            subtasks=[
                SubtaskSpec("gather", "collect sources", ("research",), requires_approval=False),
                SubtaskSpec(
                    "review-findings", "review the findings", ("review",),
                    depends_on=("gather",), requires_approval=False,
                ),
            ],
            max_workers=2,
            max_tasks=10,
            max_rounds=5,
        )
        self.assertTrue(report.complete)
        self.assertFalse(report.awaiting_approval)
        self.assertEqual(report.failed_subtasks, ())
        self.assertEqual(report.remaining_subtasks, ())
        self.assertEqual(report.validated_outputs["gather"], "facts for collect sources")
        self.assertTrue(report.validated_outputs["review-findings"].startswith("reviewed review the findings"))
        self.assertGreaterEqual(report.rounds, 2)

    def test_approval_gate_stops_without_spinning_and_can_resume(self):
        report = self.supervisor.run(
            actor_id="alice",
            idempotency_key="supervisor-approval",
            subtasks=[
                SubtaskSpec("sensitive", "prepare sensitive action", ("research",)),
            ],
            max_rounds=5,
        )
        self.assertFalse(report.complete)
        self.assertTrue(report.awaiting_approval)
        self.assertEqual(report.rounds, 1)
        task_id = report.plan.subtasks[0].task_id
        queued = self.store.get(task_id)
        self.store.approve(
            task_id, actor_id="reviewer", session_id="approval-session",
            expected_version=queued.version,
        )
        resumed = self.supervisor.resume(
            actor_id="alice", plan_id=report.plan.plan_id, max_rounds=3,
        )
        self.assertTrue(resumed.complete)
        self.assertFalse(resumed.awaiting_approval)
        self.assertEqual(resumed.validated_outputs["sensitive"], "facts for prepare sensitive action")



    def test_role_mandated_approval_is_reported_even_if_subtask_did_not_request_it(self):
        coordinator = SpecialistAgentCoordinator(
            self.runtime,
            str(Path(self.tmp.name) / "yuki.sqlite3"),
            [SpecialistAgent(
                "research", "Research specialist", frozenset({"research"}), "emit",
                requires_approval=True,
            )],
            authorize=lambda actor, action, _plan: (
                actor == "alice" and action in {"agent:plan", "agent:dispatch", "agent:read"}
            ),
        )
        supervisor = SpecialistSupervisor(coordinator, self.worker)
        report = supervisor.run(
            actor_id="alice",
            idempotency_key="supervisor-role-approval",
            subtasks=[
                SubtaskSpec("role-protected", "research safely", ("research",),
                            requires_approval=False),
            ],
        )
        self.assertTrue(report.awaiting_approval)
        self.assertFalse(report.complete)

    def test_supervisor_never_claims_unrelated_queued_tasks(self):
        unrelated = self.runtime.enqueue(
            actor_id="alice",
            idempotency_key="unrelated-queued-task",
            name="agent-research-unrelated",
            steps=[{
                "name": "unrelated", "action": "emit", "argument": "do not execute",
                "requires_approval": False,
            }],
        )
        report = self.supervisor.run(
            actor_id="alice",
            idempotency_key="supervisor-isolated-plan",
            subtasks=[
                SubtaskSpec("only-this-plan", "collect plan sources", ("research",),
                            requires_approval=False),
            ],
        )
        self.assertTrue(report.complete)
        self.assertEqual(self.store.get(unrelated.task_id).state, "queued")
        self.assertEqual(len(report.worker_results), 1)
        self.assertEqual(report.worker_results[0].task_id, report.plan.subtasks[0].task_id)


    def test_total_execution_budget_leaves_remaining_plan_tasks_queued(self):
        report = self.supervisor.run(
            actor_id="alice",
            idempotency_key="supervisor-task-budget",
            subtasks=[
                SubtaskSpec("task-a", "first", ("research",), requires_approval=False),
                SubtaskSpec("task-b", "second", ("research",), requires_approval=False),
                SubtaskSpec("task-c", "third", ("research",), requires_approval=False),
            ],
            max_tasks=1,
            max_rounds=5,
        )
        self.assertEqual(len(report.worker_results), 1)
        self.assertFalse(report.complete)
        self.assertEqual(len(report.remaining_subtasks), 2)

    def test_supervisor_requires_shared_runtime_and_bounded_budgets(self):
        other_store = WorkflowRunStore(str(Path(self.tmp.name) / "other.sqlite3"))
        other_runtime = TaskRuntime(other_store, authorize=lambda *_: True)
        other_worker = SpecialistTaskWorker(
            other_runtime, worker_id="other", service_actor_id="worker-service",
            handlers={"research": lambda _: "output"},
        )
        with self.assertRaisesRegex(ValueError, "share the same TaskRuntime"):
            SpecialistSupervisor(self.coordinator, other_worker)
        with self.assertRaisesRegex(ValueError, "max_rounds"):
            self.supervisor.resume(
                actor_id="alice", plan_id="missing", max_rounds=21,
            )


if __name__ == "__main__":
    unittest.main()
