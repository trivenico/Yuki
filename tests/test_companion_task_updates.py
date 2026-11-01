import tempfile
import unittest
from pathlib import Path

from automation.task_runtime import TaskRuntime
from automation.workflow_store import WorkflowRunStore
from companion.task_updates import CompanionTaskUpdates


class CompanionTaskUpdatesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = WorkflowRunStore(str(Path(self.temp.name) / "updates.sqlite3"))
        self.runtime = TaskRuntime(
            self.store,
            authorize=lambda actor, action, task: actor == "alice" and action in {
                "task:create", "task:read", "task:progress", "task:complete",
                "task:artifact",
            },
        )
        self.updates = CompanionTaskUpdates(self.runtime)
        self.task = self.runtime.enqueue(
            actor_id="alice", idempotency_key="companion-updates",
            name="research", steps=[{
                "name": "collect", "action": "emit", "argument": "private prompt",
                "requires_approval": False,
            }],
        )

    def test_updates_are_incremental_and_do_not_expose_task_payloads(self):
        claimed = self.store.claim_next(worker_id="worker-1")
        progressed = self.runtime.record_progress(
            actor_id="alice", task_id=self.task.task_id,
            expected_version=claimed.version,
            message="Collected three sources", percent=60,
        )
        self.runtime.record_artifact(
            actor_id="alice", task_id=self.task.task_id,
            expected_version=progressed.version,
            artifact_type="report", reference="artifact://private-reference",
            metadata={"customer": "private-customer"},
        )
        page = self.updates.updates(
            actor_id="alice", task_id=self.task.task_id, after_sequence=1, limit=2,
        )
        self.assertEqual([item.sequence for item in page], [2, 3])
        self.assertEqual(page[0].message, "Collected three sources")
        self.assertEqual(page[0].percent, 60)
        self.assertEqual(page[1].message, "Created a report artifact.")
        rendered = repr(page)
        self.assertNotIn("private-reference", rendered)
        self.assertNotIn("private-customer", rendered)
        self.assertNotIn("private prompt", rendered)

    def test_updates_require_task_read_authorization(self):
        with self.assertRaises(PermissionError):
            self.updates.updates(
                actor_id="mallory", task_id=self.task.task_id,
            )

    def test_progress_text_is_bounded_and_control_characters_removed(self):
        claimed = self.store.claim_next(worker_id="worker-1")
        self.runtime.record_progress(
            actor_id="alice", task_id=self.task.task_id,
            expected_version=claimed.version,
            message="Safe\nupdate " + ("x" * 500), percent=10,
        )
        update = self.updates.updates(
            actor_id="alice", task_id=self.task.task_id, after_sequence=1,
        )[0]
        self.assertLessEqual(len(update.message), 300)
        self.assertNotIn("\n", update.message)

    def test_maximum_valid_task_name_is_rendered_within_message_limit(self):
        store = WorkflowRunStore(str(Path(self.temp.name) / "long-name.sqlite3"))
        runtime = TaskRuntime(
            store,
            authorize=lambda actor, action, task: actor == "alice" and action in {"task:create", "task:read"},
        )
        runtime.enqueue(
            actor_id="alice", idempotency_key="long-name",
            name="n" * 100,
            steps=[{"name": "step", "action": "emit", "argument": "x", "requires_approval": False}],
        )
        update = CompanionTaskUpdates(runtime).updates(
            actor_id="alice", task_id=store.list_runs(limit=1)[0].run_id,
        )[0]
        self.assertEqual(update.message, "Started task: " + ("n" * 100))
        self.assertLessEqual(len(update.message), CompanionTaskUpdates.MAX_MESSAGE_CHARS)

    def test_invalid_pagination_is_rejected(self):
        for kwargs in ({"after_sequence": -1}, {"after_sequence": True}, {"limit": 0}, {"limit": 101}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.updates.updates(
                    actor_id="alice", task_id=self.task.task_id, **kwargs,
                )


if __name__ == "__main__":
    unittest.main()
