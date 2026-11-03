import tempfile
import unittest
from pathlib import Path

from research.planner import ResearchPlanner


class FakeTaskStore:
    def __init__(self):
        self.tasks = {}
        self.submissions = 0

    def submit(self, task_type, payload, **kwargs):
        self.submissions += 1
        key = kwargs["idempotency_key"]
        if key not in self.tasks:
            self.tasks[key] = {
                "id": "task-" + str(len(self.tasks) + 1),
                "task_type": task_type,
                "payload": payload,
                **kwargs,
            }
        return self.tasks[key]


class ResearchPlannerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Path(self.temp.name) / "research.sqlite3"
        self.tasks = FakeTaskStore()
        self.planner = ResearchPlanner(self.db, task_store=self.tasks)

    def tearDown(self):
        self.temp.cleanup()

    def test_creates_bounded_plan_without_execution(self):
        plan = self.planner.create_plan(
            "Research local-first AI architectures.",
            questions=["How is local inference handled?", "How is memory persisted?"],
        )
        self.assertEqual(plan["status"], "draft")
        self.assertIsNone(plan["task_id"])
        self.assertEqual(len(plan["steps"]), 7)
        self.assertEqual(plan["steps"][1]["kind"], "retrieve_local_knowledge")
        self.assertEqual(plan["steps"][3]["side_effects"], ["external_network_read"])
        self.assertEqual(self.tasks.submissions, 0)

    def test_questions_are_deduplicated_and_plan_persists(self):
        plan = self.planner.create_plan(
            "Research Yuki.", questions=["Memory architecture", "memory architecture"]
        )
        reopened = ResearchPlanner(self.db, task_store=self.tasks)
        loaded = reopened.get_plan(plan["plan_id"])
        self.assertEqual(loaded["questions"], ["Memory architecture"])
        self.assertEqual(loaded["steps"], plan["steps"])
        self.assertEqual(len(reopened.list_plans()), 1)

    def test_enqueues_one_approval_gated_task(self):
        plan = self.planner.create_plan("Research Yuki's local model strategy.")
        queued = self.planner.enqueue_for_approval(plan["plan_id"])
        self.assertEqual(queued["status"], "queued_for_approval")
        self.assertEqual(queued["task_id"], "task-1")
        task = self.tasks.tasks["yuki-research-plan:" + plan["plan_id"]]
        self.assertTrue(task["approval_required"])
        self.assertIsNone(task["budget_tokens"])
        self.assertIsNone(task["budget_seconds"])
        self.assertEqual(task["max_attempts"], 3)
        self.assertEqual(task["task_type"], "research.investigate")
        self.assertFalse(task["payload"]["execution_contract"]["autonomous_execution"])
        self.assertEqual(task["payload"]["execution_contract"]["graph_writes"], "verified_claims_only")

    def test_enqueue_retry_uses_idempotency_key(self):
        plan = self.planner.create_plan("Research Yuki.")
        first = self.planner.enqueue_for_approval(plan["plan_id"])
        second = self.planner.enqueue_for_approval(plan["plan_id"])
        self.assertEqual(first["task_id"], second["task_id"])
        self.assertEqual(self.tasks.submissions, 2)
        self.assertEqual(len(self.tasks.tasks), 1)

    def test_requires_task_store_to_enqueue(self):
        planner = ResearchPlanner(Path(self.temp.name) / "without-tasks.sqlite3")
        plan = planner.create_plan("Research local models.")
        with self.assertRaisesRegex(RuntimeError, "TaskStore"):
            planner.enqueue_for_approval(plan["plan_id"])

    def test_cancellation_is_only_allowed_before_enqueue(self):
        plan = self.planner.create_plan("Research local inference.")
        cancelled = self.planner.cancel_plan(plan["plan_id"])
        self.assertEqual(cancelled["status"], "cancelled")
        with self.assertRaisesRegex(ValueError, "Cancelled"):
            self.planner.enqueue_for_approval(plan["plan_id"])
        queued = self.planner.create_plan("Research secure memory.")
        self.planner.enqueue_for_approval(queued["plan_id"])
        with self.assertRaisesRegex(ValueError, "authorized cancellation API"):
            self.planner.cancel_plan(queued["plan_id"])

    def test_rejects_invalid_and_oversized_input(self):
        for goal in ("", "  ", "x" * 1001, None):
            with self.subTest(goal=str(goal)[:10]), self.assertRaises(ValueError):
                self.planner.create_plan(goal)
        with self.assertRaises(ValueError):
            self.planner.create_plan("Valid", questions=["x"] * 9)
        with self.assertRaises(ValueError):
            self.planner.create_plan("Valid", questions=["x" * 501])
        with self.assertRaises(ValueError):
            self.planner.list_plans(limit=True)

    def test_unknown_plan_is_rejected(self):
        with self.assertRaises(KeyError):
            self.planner.get_plan("missing")
        with self.assertRaises(KeyError):
            self.planner.cancel_plan("missing")


if __name__ == "__main__":
    unittest.main()
