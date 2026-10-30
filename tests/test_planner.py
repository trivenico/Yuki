import unittest

from core.planner import Planner


class PlannerTests(unittest.TestCase):

    def test_decomposes_build_goal_into_dependency_gated_specialists(self):
        tasks = Planner().decompose("Research and build a secure app, then test and report results")
        by_id = {task.task_id: task for task in tasks}
        self.assertEqual(set(by_id), {"research", "implementation", "verification", "summary"})
        self.assertEqual(by_id["implementation"].depends_on, ("research",))
        self.assertEqual(by_id["verification"].depends_on, ("implementation",))
        self.assertEqual(by_id["summary"].depends_on, ("research", "implementation", "verification"))
        self.assertTrue(all(task.requires_approval for task in tasks[:-1]))
        self.assertFalse(by_id["summary"].requires_approval)

    def test_decomposition_falls_back_to_research_and_is_bounded(self):
        tasks = Planner().decompose("Help me think through an unfamiliar request")
        self.assertEqual([task.required_capabilities for task in tasks], [("research",)])
        with self.assertRaises(ValueError):
            Planner(max_steps=1).decompose("Build and test an app and report results")

    def test_decomposition_rejects_invalid_goals(self):
        planner = Planner(max_goal_chars=5)
        for goal in ("", "      ", "x" * 6, None):
            with self.subTest(goal=goal), self.assertRaises(ValueError):
                planner.decompose(goal)

    def test_rejects_empty_and_oversized_goals(self):
        planner = Planner(max_goal_chars=8)
        for goal in ("", "   ", "x" * 9, None, 123):
            with self.subTest(goal=goal), self.assertRaises(ValueError):
                planner.propose(goal)

    def test_constructor_limits_are_validated(self):
        for value in (0, 11, True, 1.5, "5"):
            with self.subTest(max_steps=value), self.assertRaises(ValueError):
                Planner(max_steps=value)
        for value in (0, 20_001, True, 1.5, "2000"):
            with self.subTest(max_goal_chars=value), self.assertRaises(ValueError):
                Planner(max_goal_chars=value)

    def test_plan_steps_are_bounded_and_default_to_approval(self):
        plan = Planner(max_steps=3).propose("Ship a safe fix")
        self.assertEqual([step.action for step in plan.steps], ["understand", "plan", "policy_check"])
        self.assertTrue(all(step.requires_approval for step in plan.steps))

    def test_report_step_does_not_require_approval_when_included(self):
        plan = Planner(max_steps=10).propose("Ship a safe fix")
        self.assertEqual(len(plan.steps), 6)
        self.assertFalse(plan.steps[-1].requires_approval)
        self.assertEqual(plan.steps[-1].action, "report")

    def test_goal_is_trimmed_and_memory_context_is_bounded(self):
        plan = Planner().propose("  inspect the issue  ", memory_context=[str(i) for i in range(20)])
        self.assertEqual(plan.goal, "inspect the issue")
        self.assertEqual(plan.memory_context, tuple(str(i) for i in range(10)))

    def test_unbounded_memory_iterable_is_not_fully_consumed(self):
        consumed = []

        def endless_context():
            index = 0
            while True:
                consumed.append(index)
                yield index
                index += 1

        plan = Planner().propose("bounded context", memory_context=endless_context())
        self.assertEqual(plan.memory_context, tuple(str(i) for i in range(10)))
        self.assertEqual(consumed, list(range(10)))

    def test_memory_items_are_truncated(self):
        plan = Planner().propose("check limits", memory_context=["x" * 1000])
        self.assertEqual(len(plan.memory_context), 1)
        self.assertEqual(len(plan.memory_context[0]), 500)


if __name__ == "__main__":
    unittest.main()
