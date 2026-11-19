import unittest
from unittest.mock import patch

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

    def test_performance_counts_require_persisted_independent_review_evidence(self):
        goal_id = self.engine.create_goal()
        employee = self.registry.recommend({"python"}, limit=1)[0]
        reviewed = self.engine.submit(
            goal_id, "workforce.task",
            {"assigned_employee_id": employee.employee_id, "description": "Reviewed task", "review_required": True},
            idempotency_key="reviewed-performance", max_attempts=1,
        )
        self.engine.claim("performance-worker", task_type="workforce.task")
        self.engine.complete(reviewed["id"], "performance-worker", {
            "ok": True,
            "status": "completed:reviewed",
            "employee_id": employee.employee_id,
            "review_required": True,
            "review_evidence": {
                "reviewer_id": "INDEPENDENT-QA",
                "status": "approved",
                "approved": True,
            },
        })
        unreviewed = self.engine.submit(
            goal_id, "workforce.task",
            {"assigned_employee_id": employee.employee_id, "description": "Unreviewed task", "review_required": False},
            idempotency_key="unreviewed-performance", max_attempts=1,
        )
        self.engine.claim("performance-worker", task_type="workforce.task")
        self.engine.complete(unreviewed["id"], "performance-worker", {
            "ok": True, "status": "completed", "employee_id": employee.employee_id,
            "review_required": False, "review_evidence": None,
        })

        # A result that claims review but conflicts with its task payload is
        # not counted as independent QA evidence.
        inconsistent = self.engine.submit(
            goal_id, "workforce.task",
            {"assigned_employee_id": employee.employee_id, "description": "Mismatched task", "review_required": False},
            idempotency_key="mismatched-performance", max_attempts=1,
        )
        self.engine.claim("performance-worker", task_type="workforce.task")
        self.engine.complete(inconsistent["id"], "performance-worker", {
            "ok": True, "status": "completed:reviewed", "employee_id": employee.employee_id,
            "review_required": True,
            "review_evidence": {
                "reviewer_id": "INDEPENDENT-QA", "status": "approved", "approved": True,
            },
        })

        snapshot = self.dashboard.snapshot()
        row = next(row for row in snapshot["employees"] if row["employee_id"] == employee.employee_id)
        self.assertEqual(row["independently_verified_successes"], 1)
        self.assertEqual(row["successful_without_independent_review"], 1)
        self.assertEqual(snapshot["performance"]["independently_verified_successes"], 1)
        self.assertIn("no quality score", snapshot["performance"]["scoring"])

    def test_knowledge_effectiveness_reports_observational_task_outcomes(self):
        goal_id = self.engine.create_goal()
        employee = self.registry.recommend({"python"}, limit=1)[0]
        context = [{
            "knowledge_id": "lesson-observe-1", "title": "Prefer primary evidence",
            "lesson": "Use primary sources.", "source_task_id": "lesson-source",
            "reviewed_by": "human-curator",
        }, {
            # Duplicate references in one task must not inflate exposure counts.
            "knowledge_id": "lesson-observe-1", "title": "Duplicate reference",
            "lesson": "Duplicate context entry.", "source_task_id": "lesson-source",
            "reviewed_by": "human-curator",
        }]
        successful = self.engine.submit(
            goal_id, "workforce.task",
            {
                "assigned_employee_id": employee.employee_id,
                "description": "Reviewed task",
                "review_required": True,
                "knowledge_context": context,
            },
            idempotency_key="knowledge-effectiveness-success", max_attempts=1,
        )
        self.engine.claim("knowledge-effectiveness-worker", task_type="workforce.task")
        self.engine.complete(successful["id"], "knowledge-effectiveness-worker", {
            "ok": True, "status": "completed:reviewed",
            "employee_id": employee.employee_id, "review_required": True,
            "review_evidence": {
                "reviewer_id": "INDEPENDENT-QA", "status": "approved", "approved": True,
            },
        })
        failed = self.engine.submit(
            goal_id, "workforce.task",
            {
                "assigned_employee_id": employee.employee_id,
                "description": "Failed task",
                "review_required": True,
                "knowledge_context": context,
            },
            idempotency_key="knowledge-effectiveness-failure", max_attempts=1,
        )
        self.engine.claim("knowledge-effectiveness-worker", task_type="workforce.task")
        self.engine.fail(failed["id"], "knowledge-effectiveness-worker", "redacted failure")

        snapshot = self.dashboard.snapshot()
        lesson = snapshot["knowledge_effectiveness"]["lessons"][0]
        self.assertEqual(lesson["knowledge_id"], "lesson-observe-1")
        self.assertEqual(lesson["exposures"], 2)
        self.assertEqual(lesson["succeeded_tasks"], 1)
        self.assertEqual(lesson["failed_tasks"], 1)
        self.assertEqual(lesson["independently_verified_successes"], 1)
        self.assertIn("association", " ".join(snapshot["knowledge_effectiveness"]["limitations"]))

    def test_repeated_knowledge_associated_failures_create_review_prompt_only(self):
        goal_id = self.engine.create_goal()
        employee = self.registry.recommend({"python"}, limit=1)[0]
        context = [{
            "knowledge_id": "lesson-review-candidate",
            "title": "Use caching for repeated reads",
            "lesson": "Cache repeated read operations where safe.",
            "source_task_id": "lesson-source",
            "reviewed_by": "human-curator",
        }]
        for index in range(3):
            task = self.engine.submit(
                goal_id, "workforce.task",
                {
                    "assigned_employee_id": employee.employee_id,
                    "description": f"Task {index}",
                    "review_required": False,
                    "knowledge_context": context,
                },
                idempotency_key=f"knowledge-review-{index}", max_attempts=1,
            )
            self.engine.claim("knowledge-review-worker", task_type="workforce.task")
            if index < 2:
                self.engine.fail(task["id"], "knowledge-review-worker", "bounded test failure")
            else:
                self.engine.complete(task["id"], "knowledge-review-worker", {
                    "ok": True, "review_required": False,
                    "employee_id": employee.employee_id,
                })

        snapshot = self.dashboard.snapshot()
        recommendation = next(
            item for item in snapshot["decision_intelligence"]["improvement_recommendations"]
            if item["id"] == "knowledge-review:lesson-review-candidate"
        )
        self.assertEqual(recommendation["category"], "knowledge_applicability_review")
        self.assertEqual(recommendation["evidence"]["exposures"], 3)
        self.assertEqual(recommendation["evidence"]["failed_tasks"], 2)
        self.assertTrue(recommendation["requires_human_approval"])
        self.assertFalse(recommendation["automatic_action_taken"])

    def test_decision_intelligence_reports_capacity_gaps_and_department_failures(self):
        goal_id = self.engine.create_goal()
        employee = self.registry.recommend({"python"}, limit=1)[0]
        failed = self.engine.submit(
            goal_id, "workforce.task",
            {
                "assigned_employee_id": employee.employee_id,
                "description": "A failed task",
                "required_capabilities": ["python"],
            },
            idempotency_key="intelligence-failed-task", max_attempts=1,
        )
        self.engine.claim("intelligence-worker", task_type="workforce.task")
        self.engine.fail(failed["id"], "intelligence-worker", "redacted failure")
        queued = self.engine.submit(
            goal_id, "workforce.task",
            {
                "assigned_employee_id": employee.employee_id,
                "description": "Unstaffed capability demand",
                "required_capabilities": ["rare_capability_not_in_directory"],
            },
            idempotency_key="intelligence-capacity-gap", max_attempts=1,
        )

        snapshot = self.dashboard.snapshot()
        intelligence = snapshot["decision_intelligence"]
        gap = next(
            row for row in intelligence["capability_demand"]
            if row["capability"] == "rare_capability_not_in_directory"
        )
        self.assertEqual(gap["queued_or_running_tasks"], 1)
        self.assertEqual(gap["employees_with_capacity"], 0)
        self.assertEqual(gap["coverage"], "no_available_capacity")

        department = next(
            row for row in intelligence["department_signals"]
            if row["department"] == employee.department
        )
        self.assertEqual(department["failed_tasks"], 1)
        self.assertEqual(department["failure_rate"], 1.0)
        self.assertEqual(department["failure_rate_denominator"], 1)
        self.assertEqual(department["queued_tasks"], 1)
        self.assertIn("not forecasts", " ".join(intelligence["limitations"]))

    def test_improvement_recommendations_are_evidence_bound_and_never_execute(self):
        goal_id = self.engine.create_goal()
        employee = self.registry.recommend({"python"}, limit=1)[0]
        for index in range(2):
            task = self.engine.submit(
                goal_id, "workforce.task",
                {
                    "assigned_employee_id": employee.employee_id,
                    "description": f"Failure sample {index}",
                    "required_capabilities": ["python"],
                },
                idempotency_key=f"improvement-failure-{index}", max_attempts=1,
            )
            self.engine.claim("improvement-worker", task_type="workforce.task")
            self.engine.fail(task["id"], "improvement-worker", "redacted failure")

        success = self.engine.submit(
            goal_id, "workforce.task",
            {
                "assigned_employee_id": employee.employee_id,
                "description": "Successful comparison sample",
                "required_capabilities": ["python"],
            },
            idempotency_key="improvement-success-sample", max_attempts=1,
        )
        self.engine.claim("improvement-worker", task_type="workforce.task")
        self.engine.complete(success["id"], "improvement-worker", {
            "ok": True, "status": "completed", "employee_id": employee.employee_id,
            "review_required": False, "review_evidence": None,
        })

        snapshot = self.dashboard.snapshot()
        recommendations = snapshot["decision_intelligence"]["improvement_recommendations"]
        failure_review = next(
            item for item in recommendations
            if item["category"] == "failure_pattern_review"
            and item["evidence"]["department"] == employee.department
        )
        self.assertEqual(failure_review["evidence"]["failed_tasks"], 2)
        self.assertEqual(failure_review["evidence"]["failure_rate_denominator"], 2)
        self.assertTrue(failure_review["requires_human_approval"])
        self.assertFalse(failure_review["automatic_action_taken"])
        patterns = snapshot["decision_intelligence"]["failure_patterns"]
        pattern = next(
            row for row in patterns
            if row["department"] == employee.department
            and row["task_type"] == "workforce.task"
        )
        self.assertEqual(pattern["failed_tasks"], 2)
        self.assertEqual(pattern["succeeded_tasks"], 1)
        self.assertEqual(pattern["completed_tasks"], 3)
        self.assertAlmostEqual(pattern["failure_rate"], 2 / 3)
        self.assertFalse(pattern["employee_attribution"])
        self.assertFalse(pattern["raw_failure_details_included"])
        self.assertNotIn("redacted failure", str(pattern))
        recurring = next(
            item for item in recommendations
            if item["category"] == "recurring_failure_pattern"
        )
        self.assertEqual(recurring["evidence"]["required_capabilities"], ["python"])
        self.assertTrue(recurring["requires_human_approval"])
        self.assertFalse(recurring["automatic_action_taken"])
        self.assertIn("do not assign blame", " ".join(
            snapshot["decision_intelligence"]["limitations"]
        ))

    def test_recurring_failure_patterns_report_exact_verified_knowledge_tag_coverage(self):
        goal_id = self.engine.create_goal()
        employee = self.registry.recommend({"python"}, limit=1)[0]
        for index in range(3):
            task = self.engine.submit(
                goal_id, "workforce.task",
                {
                    "assigned_employee_id": employee.employee_id,
                    "description": f"Capability failure sample {index}",
                    "required_capabilities": ["python"],
                },
                idempotency_key=f"knowledge-capability-failure-{index}", max_attempts=1,
            )
            self.engine.claim("knowledge-capability-worker", task_type="workforce.task")
            if index < 2:
                self.engine.fail(task["id"], "knowledge-capability-worker", "redacted failure")
            else:
                self.engine.complete(task["id"], "knowledge-capability-worker", {
                    "ok": True, "status": "completed", "employee_id": employee.employee_id,
                    "review_required": False, "review_evidence": None,
                })

        verified_records = [
            {
                "knowledge_id": "verified-python-lesson",
                "state": "verified",
                "knowledge": {
                    "title": "Python test discipline",
                    "lesson": "Prefer deterministic tests.",
                    "domain": "engineering",
                    "tags": ["python"],
                },
            },
            {
                "knowledge_id": "verified-unrelated-lesson",
                "state": "verified",
                "knowledge": {
                    "title": "Browser guidance",
                    "lesson": "Validate browser output.",
                    "domain": "operations",
                    "tags": ["browser"],
                },
            },
        ]
        with patch.object(self.engine, "list_verified_knowledge", return_value=verified_records):
            snapshot = self.dashboard.snapshot()

        pattern = next(
            row for row in snapshot["decision_intelligence"]["failure_patterns"]
            if row["required_capabilities"] == ["python"]
        )
        self.assertEqual(pattern["verified_knowledge_ids"], ["verified-python-lesson"])
        self.assertEqual(pattern["knowledge_coverage"], "verified_tag_match_available")
        gap_signal = next(
            row for row in snapshot["decision_intelligence"]["capability_gap_signals"]
            if row["capability"] == "python"
        )
        self.assertEqual(gap_signal["assessment"], "repeated_failures_with_current_capacity")
        self.assertEqual(gap_signal["failed_tasks"], 2)
        self.assertGreaterEqual(gap_signal["employees_with_capacity"], 1)
        self.assertEqual(gap_signal["verified_knowledge_ids"], ["verified-python-lesson"])
        gap_recommendation = next(
            item for item in snapshot["decision_intelligence"]["improvement_recommendations"]
            if item["category"] == "capability_gap_review"
        )
        self.assertEqual(gap_recommendation["priority"], "medium")
        self.assertTrue(gap_recommendation["requires_human_approval"])
        self.assertFalse(gap_recommendation["automatic_action_taken"])
        development_plan = next(
            row for row in snapshot["decision_intelligence"]["skill_development_plans"]
            if row["capability"] == "python"
        )
        self.assertEqual(development_plan["failed_tasks"], 2)
        self.assertEqual(development_plan["terminal_tasks"], 3)
        self.assertEqual(development_plan["evidence_level"], "no_outcome_exposure_in_window")
        self.assertEqual(
            [row["knowledge_id"] for row in development_plan["verified_lessons"]],
            ["verified-python-lesson"],
        )
        self.assertTrue(development_plan["requires_human_approval"])
        self.assertFalse(development_plan["automatic_training_or_assignment_change"])
        development_recommendation = next(
            item for item in snapshot["decision_intelligence"]["improvement_recommendations"]
            if item["category"] == "skill_development_review"
        )
        self.assertTrue(development_recommendation["requires_human_approval"])
        self.assertFalse(development_recommendation["automatic_action_taken"])
        recommendation = next(
            item for item in snapshot["decision_intelligence"]["improvement_recommendations"]
            if item["category"] == "recurring_failure_pattern"
        )
        self.assertEqual(recommendation["evidence"]["knowledge_coverage"], "verified_tag_match_available")
        self.assertIn("does not establish lesson applicability", recommendation["recommendation"])
        self.assertTrue(recommendation["requires_human_approval"])
        self.assertFalse(recommendation["automatic_action_taken"])

        with patch.object(self.engine, "list_verified_knowledge", return_value=[]):
            without_lesson = self.dashboard.snapshot()
        missing_pattern = next(
            row for row in without_lesson["decision_intelligence"]["failure_patterns"]
            if row["required_capabilities"] == ["python"]
        )
        self.assertEqual(missing_pattern["verified_knowledge_ids"], [])
        self.assertEqual(missing_pattern["knowledge_coverage"], "no_verified_tag_match")

    def test_goal_rows_distinguish_partial_success_from_terminal_completion(self):
        goal_id = self.engine.create_goal()
        employee = self.registry.recommend({"python"}, limit=1)[0]
        succeeded = self.engine.submit(
            goal_id, "workforce.task",
            {"assigned_employee_id": employee.employee_id, "description": "Success"},
            idempotency_key="outcome-success", max_attempts=1,
        )
        self.engine.claim("outcome-worker", task_type="workforce.task")
        self.engine.complete(succeeded["id"], "outcome-worker", {
            "ok": True, "status": "completed", "employee_id": employee.employee_id,
            "review_required": False, "review_evidence": None,
        })
        failed = self.engine.submit(
            goal_id, "workforce.task",
            {"assigned_employee_id": employee.employee_id, "description": "Failure"},
            idempotency_key="outcome-failure", max_attempts=1,
        )
        self.engine.claim("outcome-worker", task_type="workforce.task")
        self.engine.fail(failed["id"], "outcome-worker", "redacted failure")

        row = next(
            item for item in self.dashboard.snapshot()["goals"]
            if item["goal_id"] == goal_id
        )
        self.assertEqual(row["state"], "completed")
        self.assertEqual(row["task_outcome"], "partially_succeeded")
        self.assertEqual(row["task_counts"]["succeeded"], 1)
        self.assertEqual(row["task_counts"]["failed"], 1)

    def test_goal_rows_report_empty_and_active_task_outcomes(self):
        empty_goal = self.engine.create_goal()
        active_goal = self.engine.create_goal()
        employee = self.registry.recommend({"python"}, limit=1)[0]
        self.engine.submit(
            active_goal, "workforce.task",
            {"assigned_employee_id": employee.employee_id, "description": "Queued"},
            idempotency_key="outcome-queued", max_attempts=1,
        )
        rows = {row["goal_id"]: row for row in self.dashboard.snapshot()["goals"]}
        self.assertEqual(rows[empty_goal]["task_outcome"], "no_tasks")
        self.assertEqual(rows[active_goal]["task_outcome"], "in_progress")

    def test_snapshot_reports_truncation_only_when_more_goals_exist(self):
        first_goal = self.engine.create_goal()
        exact_page = self.dashboard.snapshot(max_goals=1)
        self.assertEqual([row["goal_id"] for row in exact_page["goals"]], [first_goal])
        self.assertFalse(exact_page["truncated_goals"])

        self.engine.create_goal()
        truncated = self.dashboard.snapshot(max_goals=1)
        self.assertEqual(len(truncated["goals"]), 1)
        self.assertTrue(truncated["truncated_goals"])

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

    def test_goal_detail_projects_tasks_and_paginates_event_envelopes(self):
        goal_id = self.engine.create_goal()
        employee = self.registry.recommend({"python"}, limit=1)[0]
        self.engine.submit(
            goal_id, "workforce.task",
            {"assigned_employee_id": employee.employee_id, "description": "Run tests"},
            idempotency_key="goal-detail", max_attempts=1,
        )
        detail = self.dashboard.goal_detail(goal_id, event_limit=1)
        self.assertEqual(detail["goal"]["goal_id"], goal_id)
        self.assertEqual(detail["tasks"][0]["employee_id"], employee.employee_id)
        self.assertEqual(len(detail["events"]), 1)
        self.assertTrue(detail["has_more_events"])
        self.assertNotIn("detail", detail["events"][0])
        next_page = self.dashboard.goal_detail(
            goal_id, after_event_id=detail["next_event_id"], event_limit=10,
        )
        self.assertGreater(len(next_page["events"]), 0)

    def test_goal_detail_validates_bounds_and_unknown_goal(self):
        for kwargs in (
            {"after_event_id": True}, {"after_event_id": -1},
            {"event_limit": 0}, {"event_limit": 201},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.dashboard.goal_detail("missing", **kwargs)
        with self.assertRaises(Exception):
            self.dashboard.goal_detail("missing")

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
