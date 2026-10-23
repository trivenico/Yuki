import tempfile
import unittest
from pathlib import Path

from automation.workflow_store import WorkflowConflict, WorkflowRunStore, WorkflowTransitionError


class WorkflowCancellationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = WorkflowRunStore(str(Path(self.tmp.name) / "workflows.sqlite3"))

    def create(self, key="cancel-test"):
        return self.store.create(
            idempotency_key=key,
            workflow_name="cancel-test",
            definition=[{"name": "step", "action": "emit", "argument": "safe", "requires_approval": True}],
        )

    def test_cancel_atomically_transitions_and_records_one_event(self):
        run = self.create()
        cancelled = self.store.cancel_run(
            run.run_id, expected_version=run.version, actor_id="operator-1",
        )
        self.assertEqual(cancelled.state, "cancelled")
        self.assertEqual(cancelled.version, run.version + 1)
        self.assertIsNone(cancelled.lease_owner)
        events = self.store.list_task_events(run.run_id)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].kind, "task.cancelled")
        self.assertEqual(events[0].actor_id, "operator-1")
        self.assertEqual(events[0].payload["previous_state"], "queued")
        self.assertEqual(events[0].payload["reason_code"], "cancel_requested")

    def test_stale_version_cannot_cancel_or_write_event(self):
        run = self.create()
        with self.assertRaises(WorkflowConflict):
            self.store.cancel_run(run.run_id, expected_version=run.version + 1, actor_id="operator-1")
        self.assertEqual(self.store.get(run.run_id).state, "queued")
        self.assertEqual(self.store.list_task_events(run.run_id), [])

    def test_terminal_run_cannot_be_cancelled(self):
        run = self.create()
        cancelled = self.store.cancel_run(run.run_id, expected_version=run.version, actor_id="operator-1")
        with self.assertRaises(WorkflowTransitionError):
            self.store.cancel_run(cancelled.run_id, expected_version=cancelled.version, actor_id="operator-1")
        self.assertEqual(len(self.store.list_task_events(run.run_id)), 1)

    def test_cancellation_validates_actor_and_reason(self):
        run = self.create()
        for actor in ("", " ", "x" * 201, "bad" + chr(0) + "actor"):
            with self.subTest(actor=actor), self.assertRaises(ValueError):
                self.store.cancel_run(run.run_id, expected_version=run.version, actor_id=actor)
        with self.assertRaises(ValueError):
            self.store.cancel_run(run.run_id, expected_version=run.version, actor_id="operator-1",
                                  reason_code="bad reason")
        self.assertEqual(self.store.get(run.run_id).state, "queued")


if __name__ == "__main__":
    unittest.main()
