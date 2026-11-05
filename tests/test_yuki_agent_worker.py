import tempfile
import unittest
from pathlib import Path

from automation.agent_execution_observability import AgentExecutionTraceStore
from yuki_agent_runtime import TaskStore
from yuki_agent_worker import AgentWorker, TaskCancelled


class AgentWorkerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = TaskStore(Path(self.temp.name) / "tasks.sqlite3")

    def tearDown(self):
        self.temp.cleanup()

    def submit(self, task_type="echo", payload=None):
        return self.store.submit(task_type, payload or {"message": "hello"}, approval_required=False)

    def test_worker_claims_and_completes_allowlisted_handler(self):
        task = self.submit()
        worker = AgentWorker(self.store, worker_id="worker-1", handlers={
            "echo": lambda payload, ctx: {"echo": payload["message"]}
        })
        outcome = worker.run_once()
        self.assertEqual(outcome.status, "succeeded")
        self.assertEqual(outcome.task_id, task["id"])
        self.assertEqual(self.store.get(task["id"])["result"], {"echo": "hello"})

    def test_idle_worker_does_not_create_task(self):
        worker = AgentWorker(self.store, worker_id="worker-1", handlers={"echo": lambda p, c: p})
        self.assertEqual(worker.run_once().status, "idle")

    def test_unregistered_task_type_fails_without_dynamic_import(self):
        task = self.submit(task_type="untrusted.module:run")
        worker = AgentWorker(self.store, worker_id="worker-1", handlers={"echo": lambda p, c: p})
        outcome = worker.run_once()
        self.assertEqual(outcome.status, "failed")
        self.assertEqual(outcome.error_type, "HandlerNotFound")
        self.assertEqual(self.store.get(task["id"])["error"], "No allowlisted handler for task type")

    def test_handler_failure_does_not_persist_exception_message(self):
        task = self.submit()
        def bad_handler(payload, context):
            raise RuntimeError("secret=do-not-store")
        worker = AgentWorker(self.store, worker_id="worker-1", handlers={"echo": bad_handler})
        outcome = worker.run_once()
        self.assertEqual(outcome.status, "queued")  # transient errors get one bounded retry
        self.assertEqual(outcome.error_type, "RuntimeError")
        self.assertNotIn("secret", repr(self.store.events(task["id"])))

    def test_handler_can_cooperatively_detect_cancellation(self):
        task = self.submit()
        def cancelling_handler(payload, context):
            self.store.cancel(task["id"], requested_by="test")
            context.check_cancelled()
        worker = AgentWorker(self.store, worker_id="worker-1", handlers={"echo": cancelling_handler})
        outcome = worker.run_once()
        self.assertEqual(outcome.status, "cancelled")


    def test_worker_emits_privacy_safe_execution_trace(self):
        task = self.submit()
        trace_store = AgentExecutionTraceStore(str(Path(self.store.database_path).parent / "traces.sqlite3"))
        worker = AgentWorker(
            self.store, worker_id="worker-1",
            handlers={"echo": lambda payload, ctx: {"echo": payload["message"]}},
            trace_store=trace_store,
        )
        outcome = worker.run_once()
        self.assertEqual(outcome.status, "succeeded")
        traces = trace_store.list_children("root-not-used") if False else None
        with trace_store._connect() as db:
            row = db.execute(
                "SELECT execution_id,status,task_id FROM agent_execution_traces WHERE task_id=?",
                (task["id"],),
            ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["status"], "succeeded")
        events = trace_store.list_events(row["execution_id"])
        self.assertEqual(
            [event["event_type"] for event in events],
            ["execution.created", "execution.started", "execution.completed"],
        )
        self.assertEqual(worker.trace_errors, 0)

    def test_trace_store_failure_does_not_change_worker_execution(self):
        task = self.submit()
        trace_store = AgentExecutionTraceStore(str(Path(self.store.database_path).parent / "traces.sqlite3"))
        trace_store.database_path = str(Path(self.store.database_path).parent / "missing" / "trace.sqlite3")
        worker = AgentWorker(
            self.store, worker_id="worker-1",
            handlers={"echo": lambda payload, ctx: {"ok": True}},
            trace_store=trace_store,
        )
        outcome = worker.run_once()
        self.assertEqual(outcome.status, "succeeded")
        self.assertGreater(worker.trace_errors, 0)
        self.assertEqual(self.store.get(task["id"])["result"], {"ok": True})

    def test_invalid_worker_configuration_is_rejected(self):
        with self.assertRaises(ValueError):
            AgentWorker(self.store, worker_id="", handlers={"echo": lambda p, c: p})
        with self.assertRaises(ValueError):
            AgentWorker(self.store, worker_id="worker", handlers={})
        with self.assertRaises(ValueError):
            AgentWorker(self.store, worker_id="worker", handlers={"echo": lambda p, c: p}, lease_seconds=0)


if __name__ == "__main__":
    unittest.main()
