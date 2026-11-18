import unittest

from core.durable_task_engine import DurableTaskEngine
from core.planner import Planner
from core.workforce import AgentRegistry, default_workforce
from core.workforce_ceo import WorkforceCEO


class WorkforceCEOTests(unittest.TestCase):
    def setUp(self):
        self.engine = DurableTaskEngine(":memory:")
        self.addCleanup(self.engine.close)
        self.registry = AgentRegistry(default_workforce())
        self.ceo = WorkforceCEO(
            planner=Planner(max_steps=5),
            registry=self.registry,
            task_engine=self.engine,
        )

    def test_decomposes_goal_and_queues_dependency_graph(self):
        execution = self.ceo.plan_and_dispatch(
            "Research, build, test, and report on a new feature"
        )
        self.assertEqual(len(execution.plan), 4)
        self.assertEqual(len(execution.assignments), 4)
        task_by_plan_id = {
            step.task_id: assignment.task_id
            for step, assignment in zip(execution.plan, execution.assignments)
        }
        for step, assignment in zip(execution.plan, execution.assignments):
            task = self.engine.get_task(assignment.task_id)
            self.assertEqual(task["state"], "queued")
            self.assertEqual(task["goal_id"], execution.goal_id)
            if step.depends_on:
                self.assertTrue(task["dependencies"])
                self.assertEqual(
                    set(task["dependencies"]),
                    {task_by_plan_id[key] for key in step.depends_on},
                )

    def test_report_distinguishes_queued_from_completed(self):
        execution = self.ceo.plan_and_dispatch("Research the market and report findings")
        report = self.ceo.report(execution)
        self.assertEqual(report["counts"], {"queued": 2})
        self.assertFalse(report["complete"])

    def test_can_require_independent_review_for_every_dispatched_task(self):
        ceo = WorkforceCEO(
            planner=Planner(),
            registry=self.registry,
            task_engine=self.engine,
            require_independent_review=True,
        )
        execution = ceo.plan_and_dispatch("Research the market")
        for assignment in execution.assignments:
            task = self.engine.get_task(assignment.task_id)
            self.assertIs(task["payload"]["review_required"], True)

    def test_staffing_preflight_fails_before_goal_creation(self):
        empty_registry = AgentRegistry([])
        ceo = WorkforceCEO(
            planner=Planner(),
            registry=empty_registry,
            task_engine=self.engine,
        )
        with self.assertRaises(Exception):
            ceo.plan_and_dispatch("Research the market")
        # No goal is created when preflight cannot staff the plan.
        self.assertEqual(
            self.engine.claim("test-worker", task_type="workforce.task"),
            None,
        )

    def test_invalid_autonomy_tier_rejected(self):
        with self.assertRaises(ValueError):
            WorkforceCEO(
                planner=Planner(),
                registry=self.registry,
                task_engine=self.engine,
                max_autonomy_tier=True,
            )


if __name__ == "__main__":
    unittest.main()
