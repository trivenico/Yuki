import tempfile
import threading
import time
import unittest
from pathlib import Path

from core.job_queue import SQLiteJobQueue
from core.runtime_service import RuntimeService


class RuntimeServiceTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.queue = SQLiteJobQueue(Path(tmp.name) / "jobs.sqlite3")

    def test_validates_worker_bounds(self):
        with self.assertRaises(ValueError):
            RuntimeService(self.queue, {"x": lambda p: {}}, lambda *_: True, max_workers=0)

    def test_submit_and_run_one(self):
        service = RuntimeService(self.queue, {"research": lambda p: {"topic": p["topic"]}},
                                 lambda *_: True, max_workers=1)
        job = service.submit("research", {"topic": "income research"})
        self.assertEqual(job.status, "queued")
        done = service.run_one(worker_id="test-worker")
        self.assertEqual(done.status, "completed")
        self.assertEqual(done.result, {"topic": "income research"})
        self.assertTrue(service.close())

    def test_start_is_idempotent_and_close_stops_workers(self):
        service = RuntimeService(self.queue, {"noop": lambda p: {"ok": True}},
                                 lambda *_: True, max_workers=2, idle_poll_seconds=0.02)
        service.start()
        service.start()
        self.assertEqual(service.status()["worker_count"], 2)
        self.assertTrue(service.close(timeout_seconds=2))
        self.assertEqual(service.status()["worker_count"], 0)
        with self.assertRaises(RuntimeError):
            service.submit("noop", {})

    def test_worker_processes_queued_job(self):
        ran = threading.Event()
        service = RuntimeService(self.queue, {"work": lambda p: ran.set() or {"ok": True}},
                                 lambda *_: True, max_workers=2, idle_poll_seconds=0.01)
        service.submit("work", {})
        service.start()
        self.assertTrue(ran.wait(2))
        self.assertTrue(service.close(timeout_seconds=2))
        self.assertEqual(self.queue.list_jobs()[0].status, "completed")

    def test_shutdown_reports_blocked_handler(self):
        started = threading.Event()
        release = threading.Event()
        def handler(_):
            started.set()
            release.wait(2)
            return {"ok": True}
        service = RuntimeService(self.queue, {"slow": handler}, lambda *_: True,
                                 max_workers=1, idle_poll_seconds=0.01)
        service.submit("slow", {})
        service.start()
        self.assertTrue(started.wait(1))
        self.assertFalse(service.close(timeout_seconds=0.1))
        release.set()
        # A closed service does not restart, but its running handler may finish.
        time.sleep(0.1)
        self.assertEqual(service.status()["worker_count"], 0)


if __name__ == "__main__":
    unittest.main()
