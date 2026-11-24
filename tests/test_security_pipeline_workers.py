import os
import tempfile
import unittest

from core.security_operations_health import SecurityWorkerHeartbeat
from core.security_pipeline_workers import SecurityPipelineSupervisor, SecurityPipelineWorker


class SecurityPipelineWorkerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.now = 1000.0
        self.heartbeats = SecurityWorkerHeartbeat(
            os.path.join(self.tmp.name, "heartbeats.sqlite"), clock=lambda: self.now
        )

    def tearDown(self):
        self.tmp.cleanup()

    def test_successful_bounded_step_updates_heartbeat(self):
        worker = SecurityPipelineWorker("ingestion", "ingest-1", lambda: {"status": "ok"},
                                        self.heartbeats, clock=lambda: self.now)
        result = worker.run_once()
        self.assertEqual(result["status"], "ok")
        self.assertEqual(self.heartbeats.snapshot()["ingestion"], self.now)
        self.assertEqual(worker.consecutive_failures, 0)

    def test_failure_does_not_refresh_heartbeat_and_backoff_is_bounded(self):
        self.heartbeats.beat("alert_sync", "sync-1")
        worker = SecurityPipelineWorker("alert_sync", "sync-1",
            lambda: (_ for _ in ()).throw(RuntimeError("sensitive provider detail")),
            self.heartbeats, base_retry_seconds=2, max_retry_seconds=5)
        first = worker.run_once()
        second = worker.run_once()
        self.assertEqual(first["status"], "failed")
        self.assertEqual(first["retry_in_seconds"], 2)
        self.assertEqual(second["retry_in_seconds"], 4)
        self.assertEqual(worker.retry_delay(), 5)
        self.assertEqual(self.heartbeats.snapshot()["alert_sync"], self.now)
        self.assertNotIn("sensitive provider detail", str(first))

    def test_busy_step_is_a_live_worker_tick(self):
        worker = SecurityPipelineWorker("alert_dispatch", "dispatch-1",
            lambda: {"status": "busy"}, self.heartbeats, clock=lambda: self.now)
        self.assertEqual(worker.run_once()["status"], "busy")
        self.assertIn("alert_dispatch", self.heartbeats.snapshot())

    def test_supervisor_rejects_duplicate_roles(self):
        a = SecurityPipelineWorker("ingestion", "a", lambda: None, self.heartbeats)
        b = SecurityPipelineWorker("ingestion", "b", lambda: None, self.heartbeats)
        with self.assertRaises(ValueError):
            SecurityPipelineSupervisor([a, b])

    def test_supervisor_starts_and_stops_workers(self):
        worker = SecurityPipelineWorker("ingestion", "ingest-1", lambda: None,
            self.heartbeats, interval_seconds=30)
        supervisor = SecurityPipelineSupervisor([worker])
        supervisor.start()
        result = supervisor.stop(timeout_seconds=2)
        self.assertTrue(result["stopped"])
        self.assertTrue(result["workers"]["ingestion"])


    def test_failure_observer_receives_safe_metadata_and_counts_accumulate(self):
        observed = []
        worker = SecurityPipelineWorker(
            "alert_dispatch", "dispatch-2",
            lambda: (_ for _ in ()).throw(RuntimeError("secret endpoint and token")),
            self.heartbeats, base_retry_seconds=3,
            failure_observer=lambda role, failures, delay: observed.append((role, failures, delay)),
            clock=lambda: self.now,
        )
        first = worker.run_once()
        second = worker.run_once()
        self.assertEqual(first["retry_in_seconds"], 3)
        self.assertEqual(second["retry_in_seconds"], 6)
        self.assertEqual(observed, [("alert_dispatch", 1, 3), ("alert_dispatch", 2, 6)])
        self.assertEqual(worker.total_failures, 2)
        self.assertEqual(worker.last_failure_at, self.now)
        self.assertNotIn("secret endpoint", str(worker.__dict__))

    def test_failure_observer_errors_do_not_break_retry(self):
        worker = SecurityPipelineWorker(
            "alert_sync", "sync-observer",
            lambda: (_ for _ in ()).throw(RuntimeError("private detail")),
            self.heartbeats,
            failure_observer=lambda *args: (_ for _ in ()).throw(RuntimeError("telemetry secret")),
        )
        result = worker.run_once()
        self.assertEqual(result["status"], "failed")
        self.assertNotIn("telemetry secret", str(result))

    def test_supervisor_rolls_back_partial_startup(self):
        workers = [
            SecurityPipelineWorker("ingestion", "ingest-start", lambda: None,
                                   self.heartbeats, interval_seconds=30),
            SecurityPipelineWorker("alert_sync", "sync-start", lambda: None,
                                   self.heartbeats, interval_seconds=30),
        ]
        real_thread = __import__("threading").Thread
        calls = {"count": 0}

        def factory(**kwargs):
            calls["count"] += 1
            if calls["count"] == 2:
                raise RuntimeError("thread construction detail")
            return real_thread(**kwargs)

        supervisor = SecurityPipelineSupervisor(workers, thread_factory=factory)
        with self.assertRaisesRegex(RuntimeError, "security_supervisor_start_failed"):
            supervisor.start()
        result = supervisor.stop(timeout_seconds=2)
        self.assertTrue(result["stopped"])
        self.assertTrue(result["startup_failed"])
        self.assertTrue(all(worker.stop_event.is_set() for worker in workers))

    def test_shutdown_reports_blocked_operation_without_false_success(self):
        import threading
        entered = threading.Event()
        release = threading.Event()

        def blocked():
            entered.set()
            release.wait(2)

        worker = SecurityPipelineWorker(
            "ingestion", "ingest-blocked", blocked, self.heartbeats,
            interval_seconds=30,
        )
        supervisor = SecurityPipelineSupervisor([worker])
        supervisor.start()
        self.assertTrue(entered.wait(1))
        result = supervisor.stop(timeout_seconds=0.01)
        self.assertFalse(result["stopped"])
        self.assertFalse(result["workers"]["ingestion"])
        release.set()
        result = supervisor.stop(timeout_seconds=2)
        self.assertTrue(result["stopped"])


if __name__ == "__main__":
    unittest.main()
