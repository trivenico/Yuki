import tempfile
import unittest
from pathlib import Path

from automation.agent_coordinator import SpecialistAgent, SpecialistAgentCoordinator, SubtaskSpec
from automation.specialist_synthesis import synthesize_plan_results
from automation.specialist_worker import SpecialistTaskWorker
from automation.task_runtime import TaskRuntime
from automation.workflow_store import WorkflowRunStore


class SpecialistSynthesisTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        db = str(Path(self.tmp.name) / "yuki.sqlite3")
        self.runtime = TaskRuntime(
            WorkflowRunStore(db),
            authorize=lambda actor, action, task: actor == "owner",
        )
        self.coordinator = SpecialistAgentCoordinator(
            self.runtime, db,
            [SpecialistAgent("researcher", "Researcher", frozenset({"research"}), "emit")],
            authorize=lambda actor, action, resource: actor == "owner" and action in {
                "agent:plan", "agent:dispatch", "agent:read"
            },
        )

    def test_incomplete_plan_is_reported_without_dispatch_or_mutation(self):
        plan = self.coordinator.submit(
            actor_id="owner", idempotency_key="synthesis-pending",
            subtasks=(SubtaskSpec("research", "Research this", ("research",), requires_approval=False),),
        )
        before = self.coordinator.get_plan(actor_id="owner", plan_id=plan.plan_id)
        report = synthesize_plan_results(
            coordinator=self.coordinator, actor_id="owner", plan_id=plan.plan_id,
        )
        after = self.coordinator.get_plan(actor_id="owner", plan_id=plan.plan_id)
        self.assertEqual(report.status, "incomplete")
        self.assertEqual(report.incomplete_subtasks, ("research",))
        self.assertEqual(before, after)
        self.assertEqual(report.findings, ())

    def test_completed_plan_preserves_result_provenance_and_source_ids(self):
        plan = self.coordinator.submit(
            actor_id="owner", idempotency_key="synthesis-complete",
            subtasks=(SubtaskSpec("research", "Research this", ("research",), requires_approval=False),),
        )
        self.coordinator.advance(actor_id="owner", plan_id=plan.plan_id)
        worker = SpecialistTaskWorker(
            self.runtime, worker_id="worker", service_actor_id="owner",
            handlers={"researcher": lambda _: "Finding: supplied data indicates X."},
        )
        completed = worker.run_once()
        self.assertEqual(completed.state, "succeeded")
        report = synthesize_plan_results(
            coordinator=self.coordinator, actor_id="owner", plan_id=plan.plan_id,
        )
        self.assertEqual(report.status, "complete")
        self.assertEqual(len(report.findings), 1)
        finding = report.findings[0]
        self.assertEqual(finding.subtask_id, "research")
        self.assertEqual(finding.task_id, completed.task_id)
        self.assertEqual(finding.specialist, "researcher")
        self.assertFalse(finding.verified_externally)
        self.assertTrue(report.requires_human_review)
        self.assertEqual(report.conflict_review, "not_performed")

    def test_requires_both_plan_and_task_read_authorization(self):
        denied = SpecialistAgentCoordinator(
            self.runtime, str(Path(self.tmp.name) / "denied.sqlite3"),
            (), authorize=lambda actor, action, resource: False,
        )
        with self.assertRaises(PermissionError):
            synthesize_plan_results(coordinator=denied, actor_id="intruder", plan_id="missing")


if __name__ == "__main__":
    unittest.main()
