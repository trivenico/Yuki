import unittest
from datetime import datetime, timedelta, timezone

from automation.reviewer_ledger import ReviewerTaskLedger
from automation.reviewer_supervisor import inspect_reviewer_batch


class FakeLedger(ReviewerTaskLedger):
    def __init__(self, records):
        self.records = records

    def get(self, *, actor_id, task_id):
        return self.records[task_id]


class ReviewerSupervisorTests(unittest.TestCase):
    def test_reports_stale_claim_without_mutating_ledger(self):
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        old = (now - timedelta(hours=1)).isoformat()
        ledger = FakeLedger({"task-1": {
            "task_id": "task-1", "state": "in_progress",
            "version": 1, "updated_at": old,
        }})
        health = inspect_reviewer_batch(
            ledger, actor_id="auditor", task_ids=("task-1",),
            stale_after_seconds=300, now=now,
        )
        self.assertEqual(health.stale_task_ids, ("task-1",))
        self.assertEqual(health.tasks[0].recommended_action, "operator_review_required")

    def test_counts_lifecycle_states(self):
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        states = {"a": "assigned", "b": "in_progress", "c": "submitted"}
        ledger = FakeLedger({
            key: {
                "task_id": key, "state": state,
                "version": {"assigned": 0, "in_progress": 1, "submitted": 2}[state],
                "updated_at": now.isoformat(),
            } for key, state in states.items()
        })
        health = inspect_reviewer_batch(
            ledger, actor_id="auditor", task_ids=("a", "b", "c"), now=now,
        )
        self.assertEqual((health.assigned_count, health.in_progress_count, health.submitted_count), (1, 1, 1))

    def test_rejects_duplicate_ids_and_naive_time(self):
        ledger = FakeLedger({})
        with self.assertRaisesRegex(ValueError, "duplicate_reviewer_task_id"):
            inspect_reviewer_batch(ledger, actor_id="auditor", task_ids=("x", "x"))
        with self.assertRaisesRegex(ValueError, "timezone_aware_inspection_time_required"):
            inspect_reviewer_batch(ledger, actor_id="auditor", task_ids=("x",),
                                   now=datetime(2026, 1, 1))


if __name__ == "__main__":
    unittest.main()
