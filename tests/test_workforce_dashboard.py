import unittest

from core.durable_task_engine import DurableTaskEngine
from core.workforce import AgentRegistry, default_workforce
from core.workforce_dashboard import WorkforceDashboard


class WorkforceDashboardTests(unittest.TestCase):
    def setUp(self):
        self.engine = DurableTaskEngine(":memory:")
        self.addCleanup(self.engine.close)
        self.registry = AgentRegistry(default_workforce())
        self.dashboard = WorkforceDashboard(registry=self.registry, task_engine=self.engine)

    def test_snapshot_projects_registry_and_persisted_work(self):
        goal_id = self.engine.create_goal()
        employee = self.registry.recommend({"python"}, limit=1)[0]
        self.engine.submit(
            goal_id,
            "workforce.task",
            {"assigned_employee_id": employee.employee_id, "description": "test"},
            idempotency_key="dashboard-test",
            max_attempts=1,
        )
        snapshot = self.dashboard.snapshot()
        self.assertEqual(snapshot["workforce"]["total_employees"], 100)
        self.assertEqual(snapshot["workforce"]["departments"], 10)
        self.assertEqual(snapshot["tasks"]["total"], 1)
        self.assertEqual(snapshot["tasks"]["queued"], 1)
        row = next(row for row in snapshot["employees"] if row["employee_id"] == employee.employee_id)
        self.assertEqual(row["queued_tasks"], 1)
        self.assertEqual(sum(row["employees"] for row in snapshot["departments"]), 100)
        self.assertEqual(snapshot["goals"][0]["goal_id"], goal_id)

    def test_snapshot_ignores_non_workforce_task_types(self):
        goal_id = self.engine.create_goal()
        self.engine.submit(goal_id, "other.task", {}, idempotency_key="other-task")
        self.assertEqual(self.dashboard.snapshot()["tasks"]["total"], 0)

    def test_goal_limit_is_bounded_and_strict(self):
        for invalid in (True, 0, 501, 1.5):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                self.dashboard.snapshot(max_goals=invalid)

    def test_empty_database_still_reports_directory(self):
        snapshot = self.dashboard.snapshot()
        self.assertEqual(snapshot["workforce"]["total_employees"], 100)
        self.assertEqual(snapshot["tasks"]["total"], 0)
        self.assertEqual(len(snapshot["employees"]), 100)

    def test_employee_directory_filters_and_paginates(self):
        first = self.dashboard.employee_directory(department="Engineering", page=1, page_size=3)
        second = self.dashboard.employee_directory(department="Engineering", page=2, page_size=3)
        self.assertEqual(first["total"], 10)
        self.assertEqual(len(first["employees"]), 3)
        self.assertEqual(first["total_pages"], 4)
        self.assertNotEqual(
            first["employees"][0]["employee_id"],
            second["employees"][0]["employee_id"],
        )
        searched = self.dashboard.employee_directory(search="backend")
        self.assertTrue(all("backend" in " ".join(row["capabilities"]) for row in searched["employees"]))

    def test_department_detail_returns_live_roster(self):
        detail = self.dashboard.department_detail("engineering")
        self.assertEqual(detail["department"]["employees"], 10)
        self.assertEqual(len(detail["employees"]), 10)
        with self.assertRaises(KeyError):
            self.dashboard.department_detail("not-a-department")

    def test_employee_directory_rejects_invalid_filters_and_pagination(self):
        for kwargs in (
            {"page": True},
            {"page_size": 101},
            {"status": []},
            {"search": 12},
            {"department": ""},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.dashboard.employee_directory(**kwargs)


if __name__ == "__main__":
    unittest.main()
