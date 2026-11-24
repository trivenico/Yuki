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


if __name__ == "__main__":
    unittest.main()
