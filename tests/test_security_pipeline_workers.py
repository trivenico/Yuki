import os
import tempfile
import threading
import unittest
from unittest.mock import patch

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
        third = worker.run_once()
        self.assertEqual(third["retry_in_seconds"], 5)
        self.assertEqual(worker.retry_delay(), 5)
        self.assertEqual(self.heartbeats.snapshot()["alert_sync"], self.now)
        self.assertNotIn("sensitive provider detail", str(first))
        self.assertEqual(first["failure_code"], "worker_tick_failed")

    def test_failure_observer_receives_sanitized_event_and_success_resets_state(self):
        events = []
        worker = SecurityPipelineWorker(
            "ingestion", "ingest-2",
            lambda: (_ for _ in ()).throw(RuntimeError("secret detail")),
            self.heartbeats, failure_observer=events.append,
        )
        failed = worker.run_once()
        self.assertEqual(failed["failure_count"], 1)
        self.assertEqual(events[0]["failure_code"], "worker_tick_failed")
        self.assertNotIn("secret detail", str(events[0]))
        worker.operation = lambda: {"status": "ok"}
        self.assertEqual(worker.run_once()["status"], "ok")
        self.assertEqual(worker.consecutive_failures, 0)
        self.assertIsNone(worker.last_failure_code)

    def test_failure_observer_exception_does_not_break_worker(self):
        worker = SecurityPipelineWorker(
            "ingestion", "ingest-3", lambda: 1 / 0, self.heartbeats,
            failure_observer=lambda event: (_ for _ in ()).throw(RuntimeError("telemetry down")),
        )
        self.assertEqual(worker.run_once()["status"], "failed")
        self.assertEqual(worker.observer_failures, 1)

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
        with self.assertRaisesRegex(RuntimeError, "already_started"):
            supervisor.start()

    def test_partial_start_failure_stops_already_started_threads(self):
        first = SecurityPipelineWorker("ingestion", "ingest-a", lambda: None,
                                        self.heartbeats, interval_seconds=30)
        second = SecurityPipelineWorker("alert_sync", "sync-a", lambda: None,
                                         self.heartbeats, interval_seconds=30)
        supervisor = SecurityPipelineSupervisor([first, second])
        real_start = threading.Thread.start
        calls = {"count": 0}

        def fail_second_start(thread):
            calls["count"] += 1
            if calls["count"] == 2:
                raise RuntimeError("thread start failed")
            return real_start(thread)

        with patch.object(threading.Thread, "start", fail_second_start):
            with self.assertRaises(RuntimeError):
                supervisor.start()
        self.assertTrue(first.stop_event.is_set())
        self.assertTrue(second.stop_event.is_set())
        result = supervisor.stop(timeout_seconds=2)
        self.assertTrue(result["stopped"])
        # The second worker never started, so shutdown reports it stopped.
        self.assertTrue(result["workers"]["alert_sync"])

    def test_blocked_operation_reports_unclean_shutdown_then_recovers(self):
        entered = threading.Event()
        release = threading.Event()

        def blocked_operation():
            entered.set()
            release.wait()
            return None

        worker = SecurityPipelineWorker("ingestion", "ingest-blocked", blocked_operation,
                                         self.heartbeats, interval_seconds=30)
        supervisor = SecurityPipelineSupervisor([worker])
        supervisor.start()
        self.assertTrue(entered.wait(2))
        timed_out = supervisor.stop(timeout_seconds=0.05)
        self.assertFalse(timed_out["stopped"])
        self.assertFalse(timed_out["workers"]["ingestion"])
        release.set()
        recovered = supervisor.stop(timeout_seconds=2)
        self.assertTrue(recovered["stopped"])


if __name__ == "__main__":
    unittest.main()
