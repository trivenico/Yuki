import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from automation.reviewer_supervisor import inspect_reviewer_batch


class ReviewerSupervisorTests(unittest.TestCase):
    def test_reports_stale_claim_without_mutating_ledger(self):
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        old = (now - timedelta(hours=1)).isoformat()
        ledger = type("Ledger", (), {"get": lambda self, **kwargs: {
            "task_id": kwargs["task_id"], "state": "in_progress",
            "version": 1, "updated_at": old,
        }})()
        health = inspect_reviewer_batch(
            ledger, actor_id="auditor", task_ids=("task-1",),
            stale_after_seconds=300, now=now,
        )
        self.assertEqual(health.stale_task_ids, ("task-1",))
        self.assertEqual(health.tasks[0].recommended_action, "operator_review_required")

    def test_counts_lifecycle_states(self):
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        states = {"a": "assigned", "b": "in_progress", "c": "submitted"}
        ledger = type("Ledger", (), {"get": lambda self, **kwargs: {
            "task_id": kwargs["task_id"], "state": states[kwargs["task_id"]],
            "version": {"assigned": 0, "in_progress": 1, "submitted": 2}[states[kwargs["task_id"]]],
            "updated_at": now.isoformat(),
        }})()
        health = inspect_reviewer_batch(
            ledger, actor_id="auditor", task_ids=("a", "b", "c"), now=now,
        )
        self.assertEqual((health.assigned_count, health.in_progress_count, health.submitted_count), (1, 1, 1))

    def test_rejects_duplicate_ids_and_naive_time(self):
        ledger = type("Ledger", (), {"get": lambda self, **kwargs: {}})()
        with self.assertRaisesRegex(ValueError, "duplicate_reviewer_task_id"):
            inspect_reviewer_batch(ledger, actor_id="auditor", task_ids=("x", "x"))
        with self.assertRaisesRegex(ValueError, "timezone_aware_inspection_time_required"):
            inspect_reviewer_batch(ledger, actor_id="auditor", task_ids=("x",),
                                   now=datetime(2026, 1, 1))


if __name__ == "__main__":
    unittest.main()
