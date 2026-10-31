import tempfile
import unittest
from pathlib import Path

from automation.task_event_stream import TaskEventStream
from automation.task_runtime import TaskRuntime
from automation.workflow_store import WorkflowRunStore


class TaskEventStreamTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        store = WorkflowRunStore(str(Path(self.temp.name) / "events.sqlite3"))
        self.runtime = TaskRuntime(
            store,
            authorize=lambda actor, action, task: (
                actor == "alice" and action in {"task:create", "task:read"}
            ),
        )
        self.stream = TaskEventStream(self.runtime)
        self.task = self.runtime.enqueue(
            actor_id="alice", idempotency_key="event-stream-1",
            name="Research", steps=[{
                "name": "summarize", "action": "emit",
                "argument": "Summarize the supplied material",
                "requires_approval": True,
            }],
        )

    def test_reads_durable_events_and_emits_sse_with_resume_cursor(self):
        batch, frame = self.stream.read_sse(
            actor_id="alice", task_id=self.task.task_id,
        )
        self.assertEqual(len(batch.events), 1)
        self.assertEqual(batch.events[0].kind, "task.created")
        self.assertEqual(batch.next_sequence, 1)
        self.assertFalse(batch.has_more)
        self.assertIn("id: 1\n", frame)
        self.assertIn("event: task.created\n", frame)
        self.assertIn('"task_id"', frame)
        replay = self.stream.read_batch(
            actor_id="alice", task_id=self.task.task_id, after_sequence=1,
        )
        self.assertEqual(replay.events, ())
        self.assertEqual(replay.next_sequence, 1)

    def test_authorization_is_enforced_before_event_reads(self):
        with self.assertRaises(PermissionError):
            self.stream.read_batch(actor_id="mallory", task_id=self.task.task_id)

    def test_cursor_and_page_size_are_strictly_bounded(self):
        with self.assertRaises(ValueError):
            self.stream.read_batch(
                actor_id="alice", task_id=self.task.task_id, after_sequence=-1,
            )
        with self.assertRaises(ValueError):
            self.stream.read_batch(
                actor_id="alice", task_id=self.task.task_id, limit=501,
            )

    def test_sse_encoder_rejects_untyped_events(self):
        with self.assertRaises(TypeError):
            self.stream.encode_sse([{"kind": "task.created"}])


if __name__ == "__main__":
    unittest.main()
