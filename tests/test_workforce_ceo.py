import unittest

from core.durable_task_engine import DurableTaskEngine
from core.planner import PlanTask, Planner
from core.workforce import AgentProfile, AgentRegistry, default_workforce
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
        # The dependency gate prevents implementation from being claimed while
        # research is still queued; after research succeeds, implementation opens.
        first = self.engine.claim("ceo-test-worker", task_type="workforce.task")
        self.assertEqual(first["id"], task_by_plan_id["research"])
        self.engine.complete(first["id"], "ceo-test-worker", {"ok": True})
        second = self.engine.claim("ceo-test-worker", task_type="workforce.task")
        self.assertEqual(second["id"], task_by_plan_id["implementation"])

    def test_report_distinguishes_queued_from_completed(self):
        execution = self.ceo.plan_and_dispatch("Research the market and report findings")
        report = self.ceo.report(execution)
        self.assertEqual(report["counts"], {"queued": 2})
        self.assertEqual(sum(value["total"] for value in report["departments"].values()), 2)
        self.assertEqual(
            sum(value.get("queued", 0) for value in report["departments"].values()),
            2,
        )
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


    def test_capacity_reservations_spread_parallel_work_across_employees(self):
        class TwoCodingSteps(Planner):
            def decompose(self, goal):
                return self.validate_tasks((
                    PlanTask("build_a", "Build component A", ("coding",)),
                    PlanTask("build_b", "Build component B", ("coding",)),
                ))

        employees = [
            AgentProfile(
                employee_id=f"ENG-{index:03d}",
                name=f"Engineer {index}",
                department="Engineering",
                role="Engineer",
                capabilities=frozenset({"coding"}),
                reports_to="YUKI-CEO",
                max_concurrent_tasks=1,
            )
            for index in (1, 2)
        ]
        ceo = WorkforceCEO(
            planner=TwoCodingSteps(),
            registry=AgentRegistry(employees),
            task_engine=self.engine,
        )
        execution = ceo.plan_and_dispatch("Build two components")
        self.assertEqual(
            len({assignment.employee_id for assignment in execution.assignments}),
            2,
        )

    def test_capacity_preflight_rejects_oversubscribed_plan(self):
        class TwoCodingSteps(Planner):
            def decompose(self, goal):
                return self.validate_tasks((
                    PlanTask("build_a", "Build component A", ("coding",)),
                    PlanTask("build_b", "Build component B", ("coding",)),
                ))

        employee = AgentProfile(
            employee_id="ENG-ONLY",
            name="Only Engineer",
            department="Engineering",
            role="Engineer",
            capabilities=frozenset({"coding"}),
            reports_to="YUKI-CEO",
            max_concurrent_tasks=1,
        )
        ceo = WorkforceCEO(
            planner=TwoCodingSteps(),
            registry=AgentRegistry([employee]),
            task_engine=self.engine,
        )
        with self.assertRaises(Exception):
            ceo.plan_and_dispatch("Build two components")
        self.assertIsNone(self.engine.claim("capacity-check", task_type="workforce.task"))

    def test_partial_dag_submission_reuses_persisted_assignments_on_retry(self):
        class TwoCodingSteps(Planner):
            def decompose(self, goal):
                return self.validate_tasks((
                    PlanTask("build_a", "Build component A", ("coding",)),
                    PlanTask("build_b", "Build component B", ("coding",)),
                ))

        employees = [
            AgentProfile(
                employee_id=f"ENG-{index:03d}",
                name=f"Engineer {index}",
                department="Engineering",
                role="Engineer",
                capabilities=frozenset({"coding"}),
                reports_to="YUKI-CEO",
                max_concurrent_tasks=1,
            )
            for index in (1, 2)
        ]
        ceo = WorkforceCEO(
            planner=TwoCodingSteps(),
            registry=AgentRegistry(employees),
            task_engine=self.engine,
        )
        goal_id = self.engine.create_goal(owner_id="owner-1")
        original_submit = ceo.router.submit
        calls = {"count": 0}

        def fail_after_first(**kwargs):
            calls["count"] += 1
            if calls["count"] == 2:
                raise RuntimeError("simulated process interruption")
            return original_submit(**kwargs)

        ceo.router.submit = fail_after_first
        with self.assertRaisesRegex(RuntimeError, "simulated process interruption"):
            ceo.plan_and_dispatch("Build two components", goal_id=goal_id)

        first = self.engine.get_task_by_idempotency(goal_id, f"ceo:{goal_id}:build_a")
        self.assertIsNotNone(first)
        ceo.router.submit = original_submit
        recovered = ceo.plan_and_dispatch("Build two components", goal_id=goal_id)

        self.assertEqual(len(self.engine.list_tasks(goal_id)), 2)
        self.assertEqual(recovered.assignments[0].task_id, first["id"])
        self.assertEqual(
            {item.employee_id for item in recovered.assignments},
            {"ENG-001", "ENG-002"},
        )
        self.assertEqual(
            [item["state"] for item in self.engine.list_tasks(goal_id)],
            ["queued", "queued"],
        )

    def test_existing_goal_rejects_changed_planner_dag(self):
        class MutablePlanner(Planner):
            def __init__(self):
                self.changed = False

            def decompose(self, goal):
                task_id = "build_b" if self.changed else "build_a"
                return self.validate_tasks((
                    PlanTask(task_id, "Build component", ("coding",)),
                ))

        employee = AgentProfile(
            employee_id="ENG-PLAN",
            name="Plan Engineer",
            department="Engineering",
            role="Engineer",
            capabilities=frozenset({"coding"}),
            reports_to="YUKI-CEO",
        )
        planner = MutablePlanner()
        ceo = WorkforceCEO(
            planner=planner,
            registry=AgentRegistry([employee]),
            task_engine=self.engine,
        )
        goal_id = self.engine.create_goal(owner_id="owner-1")
        first = ceo.plan_and_dispatch("Build component", goal_id=goal_id)
        planner.changed = True
        with self.assertRaisesRegex(Exception, "CEO plan changed"):
            ceo.plan_and_dispatch("Build component", goal_id=goal_id)
        self.assertEqual(len(self.engine.list_tasks(goal_id)), 1)
        self.assertEqual(self.engine.list_tasks(goal_id)[0]["id"], first.assignments[0].task_id)

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
