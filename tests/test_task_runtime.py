import tempfile
import unittest
from pathlib import Path

from automation.task_runtime import TaskRuntime
from automation.workflow_store import WorkflowConflict, WorkflowRunStore, WorkflowTransitionError


class TaskRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = WorkflowRunStore(str(Path(self.tmp.name) / "tasks.sqlite3"))
        self.grants = {"task:create", "task:read", "task:progress", "task:artifact",
                       "task:complete", "task:fail", "task:cancel"}
        self.runtime = TaskRuntime(
            self.store,
            authorize=lambda actor, action, task: (actor == "alice" and action in self.grants) or (actor == "supervisor" and action == "task:recover"),
        )
        self.steps = [{"name": "review", "action": "emit", "argument": "inspect",
                       "requires_approval": False}]

    def create(self, key="task-1"):
        return self.runtime.enqueue(actor_id="alice", idempotency_key=key,
                                    name="test-task", steps=self.steps)

    def claim(self, task):
        return self.store.claim_next(worker_id="worker-a")

    def test_enqueue_is_idempotent_and_event_is_persisted(self):
        first = self.create()
        second = self.create()
        self.assertEqual(first.task_id, second.task_id)
        events = self.runtime.events(actor_id="alice", task_id=first.task_id)
        self.assertEqual([e.kind for e in events], ["task.created"])
        self.assertEqual(events[0].payload["step_count"], 1)

    def test_authorization_fails_closed(self):
        with self.assertRaises(PermissionError):
            TaskRuntime(self.store).enqueue(actor_id="alice", idempotency_key="no-auth",
                                            name="task", steps=self.steps)
        broken = TaskRuntime(self.store, authorize=lambda *_: (_ for _ in ()).throw(RuntimeError()))
        with self.assertRaises(PermissionError):
            broken.enqueue(actor_id="alice", idempotency_key="broken-auth",
                           name="task", steps=self.steps)

    def test_progress_is_versioned_and_durable(self):
        task = self.create()
        claimed = self.claim(task)
        updated = self.runtime.record_progress(actor_id="alice", task_id=task.task_id,
            expected_version=claimed.version, message="Analyzing", percent=25)
        self.assertEqual(updated.version, claimed.version + 1)
        with self.assertRaises(WorkflowConflict):
            self.runtime.record_progress(actor_id="alice", task_id=task.task_id,
                expected_version=claimed.version, message="stale")
        self.assertEqual([e.kind for e in self.runtime.events(actor_id="alice", task_id=task.task_id)],
                         ["task.created", "task.progress"])

    def test_artifact_is_journaled(self):
        task = self.create()
        claimed = self.claim(task)
        self.runtime.record_artifact(actor_id="alice", task_id=task.task_id,
            expected_version=claimed.version, artifact_type="report",
            reference="artifact://report-1", metadata={"format": "markdown"})
        event = self.runtime.events(actor_id="alice", task_id=task.task_id)[-1]
        self.assertEqual(event.kind, "task.artifact")
        self.assertEqual(event.payload["reference"], "artifact://report-1")

    def test_only_current_worker_can_complete(self):
        task = self.create()
        claimed = self.claim(task)
        with self.assertRaises(WorkflowTransitionError):
            self.runtime.complete(actor_id="alice", task_id=task.task_id,
                expected_version=claimed.version, worker_id="worker-b", result={})
        done = self.runtime.complete(actor_id="alice", task_id=task.task_id,
            expected_version=claimed.version, worker_id="worker-a", result={"ok": True})
        self.assertEqual(done.state, "succeeded")
        self.assertEqual(self.runtime.events(actor_id="alice", task_id=task.task_id)[-1].kind,
                         "task.completed")

    def test_expired_lease_cannot_complete(self):
        task = self.create()
        claimed = self.claim(task)
        with self.store._connect() as db:
            db.execute("UPDATE workflow_runs SET lease_expires_at='2000-01-01T00:00:00+00:00' WHERE run_id=?",
                       (task.task_id,))
        with self.assertRaises(WorkflowConflict):
            self.runtime.complete(actor_id="alice", task_id=task.task_id,
                expected_version=claimed.version, worker_id="worker-a", result={})

    def test_cancel_is_versioned_and_terminal(self):
        task = self.create()
        cancelled = self.runtime.cancel(actor_id="alice", task_id=task.task_id,
                                         expected_version=task.version)
        self.assertEqual(cancelled.state, "cancelled")
        self.assertEqual(self.runtime.events(actor_id="alice", task_id=task.task_id)[-1].kind,
                         "task.cancelled")
        with self.assertRaises(WorkflowTransitionError):
            self.runtime.cancel(actor_id="alice", task_id=task.task_id,
                                 expected_version=cancelled.version)

    def test_invalid_progress_payload_is_rejected(self):
        task = self.create()
        claimed = self.claim(task)
        with self.assertRaises(ValueError):
            self.runtime.record_progress(actor_id="alice", task_id=task.task_id,
                expected_version=claimed.version, message="x" * 1001)
        with self.assertRaises(ValueError):
            self.runtime.record_progress(actor_id="alice", task_id=task.task_id,
                expected_version=claimed.version, message="ok", percent=True)

    def test_expired_lease_recovery_is_authorized_and_journaled(self):
        task = self.create("recover-expired")
        claimed = self.claim(task)
        with self.store._connect() as db:
            db.execute("UPDATE workflow_runs SET lease_expires_at='2000-01-01T00:00:00+00:00' WHERE run_id=?",
                       (task.task_id,))
        with self.assertRaises(PermissionError):
            self.runtime.recover_expired(actor_id="alice")
        recovered = self.runtime.recover_expired(actor_id="supervisor")
        self.assertEqual([item.task_id for item in recovered], [task.task_id])
        self.assertEqual(recovered[0].state, "interrupted")
        events = self.runtime.events(actor_id="alice", task_id=task.task_id)
        self.assertEqual(events[-1].kind, "task.interrupted")
        self.assertFalse(events[-1].payload["automatic_replay"])


if __name__ == "__main__":
    unittest.main()
