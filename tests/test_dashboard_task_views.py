import unittest
from types import SimpleNamespace

from ui.task_views import render_task_events, render_task_summaries


class FakeTaskRuntime:
    def __init__(self):
        self.summary_calls = []
        self.event_calls = []

    def list_task_summaries(self, *, actor_id, limit):
        self.summary_calls.append((actor_id, limit))
        return [SimpleNamespace(
            task_id="task-123", name="review", state="running", version=4,
            attempts=1, created_at="2026-10-30T00:00:00Z",
            updated_at="2026-10-30T00:01:00Z", lease_owner="worker-a",
            lease_expires_at="2026-10-30T00:02:00Z",
        )]

    def events(self, *, actor_id, task_id, after_sequence, limit):
        self.event_calls.append((actor_id, task_id, after_sequence, limit))
        return [SimpleNamespace(
            sequence=8, kind="task.progress", created_at="2026-10-30T00:01:00Z",
            payload={"message": "private customer details"},
        )]


class DashboardTaskViewTests(unittest.TestCase):
    def setUp(self):
        self.runtime = FakeTaskRuntime()

    def test_task_summary_view_shows_metadata_only(self):
        rendered = render_task_summaries(self.runtime, "dashboard-user")
        self.assertIn("task-123", rendered)
        self.assertIn("running", rendered)
        self.assertIn("worker=worker-a", rendered)
        self.assertNotIn("private customer details", rendered)
        self.assertEqual(self.runtime.summary_calls, [("dashboard-user", 50)])

    def test_event_view_uses_cursor_and_omits_payload(self):
        rendered = render_task_events(
            self.runtime, "dashboard-user", "task-123",
            after_sequence=7, limit=20,
        )
        self.assertIn("#8 | task.progress", rendered)
        self.assertIn("Next cursor: 8", rendered)
        self.assertNotIn("private customer details", rendered)
        self.assertEqual(
            self.runtime.event_calls, [("dashboard-user", "task-123", 7, 20)],
        )

    def test_views_validate_bounds_and_require_actor(self):
        with self.assertRaises(ValueError):
            render_task_summaries(self.runtime, "dashboard-user", limit=101)
        with self.assertRaises(ValueError):
            render_task_events(self.runtime, "dashboard-user", "task-123", after_sequence=-1)
        with self.assertRaises(ValueError):
            render_task_events(self.runtime, "", "task-123")


if __name__ == "__main__":
    unittest.main()
