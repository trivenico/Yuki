import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from yuki_agent_runtime import TaskStore
from yuki_agent_worker import WorkerOutcome
from yuki_worker_service import _build_worker, run_worker_loop


class FakeWorker:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0

    def run_once(self):
        self.calls += 1
        if self.outcomes:
            return self.outcomes.pop(0)
        return WorkerOutcome(task_id=None, status="idle")


class WorkerServiceTests(unittest.TestCase):
    def test_loop_stops_when_stop_event_is_set(self):
        event = threading.Event()
        worker = FakeWorker([
            WorkerOutcome(task_id=None, status="idle"),
            WorkerOutcome(task_id=None, status="idle"),
        ])
        observed = []
        def observe(outcome):
            observed.append(outcome.status)
            if len(observed) == 1:
                event.set()
        run_worker_loop(worker, event, idle_sleep_seconds=0.05, on_outcome=observe)
        self.assertEqual(worker.calls, 1)
        self.assertEqual(observed, ["idle"])

    def test_loop_continues_after_success_and_sleeps_when_idle(self):
        event = threading.Event()
        worker = FakeWorker([
            WorkerOutcome(task_id="task-1", status="succeeded"),
            WorkerOutcome(task_id=None, status="idle"),
        ])
        observed = []
        def observe(outcome):
            observed.append(outcome.status)
            if len(observed) == 2:
                event.set()
        run_worker_loop(worker, event, idle_sleep_seconds=0.05, on_outcome=observe)
        self.assertEqual(observed, ["succeeded", "idle"])

    def test_poll_interval_bounds_are_validated(self):
        with self.assertRaises(ValueError):
            run_worker_loop(FakeWorker([]), threading.Event(), idle_sleep_seconds=0)
        with self.assertRaises(ValueError):
            run_worker_loop(FakeWorker([]), threading.Event(), idle_sleep_seconds=61)

    def test_build_worker_uses_configured_mock_provider_and_database(self):
        with tempfile.TemporaryDirectory() as temp:
            db = Path(temp) / "tasks.sqlite3"
            env = {
                "YUKI_PROVIDER": "mock",
                "YUKI_TASK_DB": str(db),
                "YUKI_WORKER_ID": "test-worker",
            }
            worker = _build_worker(env)
            self.assertEqual(worker.worker_id, "test-worker")
            task_store = TaskStore(db)
            task_store.submit("model.generate", {"prompt": "hello"}, approval_required=False)
            outcome = worker.run_once()
            self.assertEqual(outcome.status, "succeeded")

    def test_build_worker_rejects_unknown_provider(self):
        with tempfile.TemporaryDirectory() as temp:
            env = {"YUKI_PROVIDER": "made-up", "YUKI_TASK_DB": str(Path(temp) / "db.sqlite3")}
            with self.assertRaisesRegex(RuntimeError, "Cannot configure provider"):
                _build_worker(env)


if __name__ == "__main__":
    unittest.main()
