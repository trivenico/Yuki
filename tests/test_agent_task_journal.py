import tempfile
import unittest
from pathlib import Path

from automation.agent_task_journal import AgentTaskJournal
from automation.task_runtime import TaskRuntime
from automation.workflow_store import WorkflowConflict, WorkflowRunStore, WorkflowTransitionError


class AgentTaskJournalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = WorkflowRunStore(str(Path(self.tmp.name) / "agent.sqlite3"))
        grants = {"task:create", "task:read", "task:progress", "task:complete"}
        self.runtime = TaskRuntime(
            self.store,
            authorize=lambda actor, action, task: actor == "agent-worker" and action in grants,
        )
        task = self.runtime.enqueue(
            actor_id="agent-worker",
            idempotency_key="agent-run-1",
            name="agent-task",
            steps=[{"name": "run", "action": "emit", "argument": "start",
                    "requires_approval": False}],
        )
        self.claimed = self.store.claim_next(worker_id="worker-1")
        self.assertEqual(task.task_id, self.claimed.run_id)

    def journal(self):
        return AgentTaskJournal(
            self.runtime, actor_id="agent-worker",
            task_id=self.claimed.run_id, worker_id="worker-1",
        )

    def test_lifecycle_events_are_durably_recorded_as_progress(self):
        journal = self.journal()
        journal("agent.started", {"user_chars": 9000, "tool_count": 2})
        journal("agent.tools_dispatched", {"count": 1, "total": 1})
        journal("agent.completed", {"model_turns": 2, "tool_calls": 1})
        events = self.runtime.events(actor_id="agent-worker", task_id=self.claimed.run_id)
        progress = [event for event in events if event.kind == "task.progress"]
        self.assertEqual(len(progress), 3)
        self.assertIn("Agent execution started", progress[0].payload["message"])
        self.assertNotIn("user_chars", progress[0].payload["message"])
        self.assertEqual(journal.version, self.runtime.get(
            actor_id="agent-worker", task_id=self.claimed.run_id).version)

    def test_sensitive_details_are_not_persisted(self):
        journal = self.journal()
        journal("agent.model_failed", {
            "error_type": "TimeoutError",
            "prompt": "private user prompt",
            "api_key": "secret",
            "tool_arguments": {"password": "secret"},
        })
        events = self.runtime.events(actor_id="agent-worker", task_id=self.claimed.run_id)
        serialized = str(events[-1].payload)
        self.assertNotIn("private user prompt", serialized)
        self.assertNotIn("secret", serialized)
        self.assertIn("TimeoutError", serialized)

    def test_stale_version_fails_closed(self):
        journal = self.journal()
        self.runtime.record_progress(
            actor_id="agent-worker", task_id=self.claimed.run_id,
            expected_version=self.claimed.version, message="external update",
        )
        with self.assertRaises(WorkflowConflict):
            journal("agent.started", {})
    
    def test_wrong_worker_and_expired_lease_are_rejected(self):
        with self.assertRaises(WorkflowTransitionError):
            AgentTaskJournal(
                self.runtime, actor_id="agent-worker",
                task_id=self.claimed.run_id, worker_id="other-worker",
            )
        with self.store._connect() as db:
            db.execute(
                "UPDATE workflow_runs SET lease_expires_at=? WHERE run_id=?",
                ("2000-01-01T00:00:00+00:00", self.claimed.run_id),
            )
        with self.assertRaises(WorkflowConflict):
            self.journal()

    def test_unsupported_events_and_bad_payloads_are_rejected(self):
        journal = self.journal()
        with self.assertRaises(ValueError):
            journal("agent.raw_prompt", {})
        with self.assertRaises(ValueError):
            journal("agent.started", "not-an-object")

    def test_missing_read_or_progress_permission_fails_closed(self):
        restricted = TaskRuntime(
            self.store,
            authorize=lambda actor, action, task: action == "task:read",
        )
        journal = AgentTaskJournal(
            restricted, actor_id="agent-worker",
            task_id=self.claimed.run_id, worker_id="worker-1",
        )
        with self.assertRaises(PermissionError):
            journal("agent.started", {})


if __name__ == "__main__":
    unittest.main()
