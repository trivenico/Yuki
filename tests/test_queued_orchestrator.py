import tempfile
import unittest
from pathlib import Path

from core.job_queue import SQLiteJobQueue
from core.queued_orchestrator import QueuedTaskOrchestrator

class QueuedTaskOrchestratorTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.queue = SQLiteJobQueue(Path(tmp.name) / "queue.sqlite3")
        self.authorized = True
        self.checks = []
        def authorize(task_type, payload):
            self.checks.append((task_type, dict(payload)))
            return self.authorized
        self.authorize = authorize

    def test_submit_and_dispatch_recheck_authorization(self):
        seen = []
        runtime = QueuedTaskOrchestrator(
            self.queue, {"research": lambda payload: seen.append(payload) or {"ok": True}},
            self.authorize, worker_id="worker-1")
        job = runtime.submit("research", {"topic": "income research"})
        self.assertEqual(job.status, "queued")
        done = runtime.run_one()
        self.assertEqual(done.status, "completed")
        self.assertEqual(seen, [{"topic": "income research"}])
        self.assertEqual(len(self.checks), 2)

    def test_denied_submission_never_queues(self):
        self.authorized = False
        runtime = QueuedTaskOrchestrator(self.queue, {"research": lambda p: {}}, self.authorize, worker_id="w")
        with self.assertRaises(PermissionError):
            runtime.submit("research", {})
        self.assertEqual(self.queue.list_jobs(), [])

    def test_denied_execution_fails_closed(self):
        calls = []
        runtime = QueuedTaskOrchestrator(self.queue, {"research": lambda p: calls.append(p) or {}}, self.authorize, worker_id="w")
        runtime.submit("research", {})
        self.authorized = False
        job = runtime.run_one()
        self.assertEqual(job.status, "failed")
        self.assertEqual(calls, [])

    def test_unregistered_task_rejected(self):
        runtime = QueuedTaskOrchestrator(self.queue, {}, self.authorize, worker_id="w")
        with self.assertRaises(ValueError):
            runtime.submit("shell", {"command": "unsafe"})

    def test_handler_result_must_be_object_and_error_is_sanitized(self):
        runtime = QueuedTaskOrchestrator(self.queue, {"bad": lambda p: ["not", "object"]}, self.authorize, worker_id="w")
        runtime.submit("bad", {})
        job = runtime.run_one()
        self.assertEqual(job.status, "queued")  # bounded retry budget still available
        self.assertEqual(job.error, "TypeError: task handler failed")
        self.assertNotIn("Traceback", job.error)

    def test_idle_worker_returns_none(self):
        runtime = QueuedTaskOrchestrator(self.queue, {}, self.authorize, worker_id="w")
        self.assertIsNone(runtime.run_one())

if __name__ == "__main__":
    unittest.main()
