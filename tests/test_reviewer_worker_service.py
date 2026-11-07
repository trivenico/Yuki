"""Tests for bounded reviewer worker supervision."""
from __future__ import annotations

import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from automation.reviewer_worker_service import ReviewerWorkerService
from automation.reviewer_worker_registry import ReviewerWorkerRegistry


class ConcurrentWorker:
    worker_id = "reviewer-a"

    def __init__(self):
        self.lock = threading.Lock()
        self.active = 0
        self.maximum_active = 0
        self.calls = 0

    def run_once(self, *, limit=1):
        with self.lock:
            self.active += 1
            self.calls += 1
            self.maximum_active = max(self.maximum_active, self.active)
        time.sleep(0.02)
        with self.lock:
            self.active -= 1
        return SimpleNamespace(claimed=1, completed=1)


class ReviewerWorkerServiceTests(unittest.TestCase):
    def test_runs_bounded_parallel_cycles_and_emits_stopped_heartbeat(self):
        worker = ConcurrentWorker()
        heartbeats = []
        service = None

        def heartbeat(value):
            heartbeats.append(value)
            if value.completed_dispatches >= 2:
                service.stop()

        service = ReviewerWorkerService(
            worker, heartbeat_callback=heartbeat,
            max_concurrency=2, poll_interval_seconds=0.05,
        )
        report = service.run_forever()
        self.assertTrue(report.stopped)
        self.assertGreaterEqual(report.completed_dispatches, 2)
        self.assertLessEqual(worker.maximum_active, 2)
        self.assertGreaterEqual(worker.maximum_active, 2)
        self.assertEqual(heartbeats[-1].state, "stopped")
        self.assertEqual(heartbeats[-1].active_slots, 0)

    def test_stop_before_start_returns_cleanly(self):
        worker = ConcurrentWorker()
        heartbeats = []
        service = ReviewerWorkerService(worker, heartbeat_callback=heartbeats.append)
        service.stop()
        report = service.run_forever()
        self.assertEqual(report.cycles, 0)
        self.assertEqual(heartbeats[-1].state, "stopped")

    def test_registry_registration_slot_and_shutdown_are_integrated(self):
        with tempfile.TemporaryDirectory() as tmp:
            registry = ReviewerWorkerRegistry(
                Path(tmp) / "workers.sqlite3",
                authorize=lambda actor, action, worker: actor == worker,
            )
            worker = ConcurrentWorker()
            service = None

            def heartbeat(value):
                if value.completed_dispatches >= 1:
                    service.stop()

            service = ReviewerWorkerService(
                worker, heartbeat_callback=heartbeat,
                max_concurrency=1, poll_interval_seconds=0.05,
                registry=registry, registry_actor_id="reviewer-a",
                instance_id="instance-a",
            )
            report = service.run_forever()
            self.assertTrue(report.stopped)
            statuses = registry.list_workers(
                actor_id="reviewer-a", worker_id="reviewer-a",
            )
            self.assertEqual(len(statuses), 1)
            self.assertEqual(statuses[0].state, "stopped")
            self.assertEqual(statuses[0].active_slots, 0)

    def test_rejects_unbounded_or_invalid_configuration(self):
        worker = ConcurrentWorker()
        with self.assertRaisesRegex(ValueError, "invalid_reviewer_worker_concurrency"):
            ReviewerWorkerService(worker, heartbeat_callback=lambda _: None, max_concurrency=33)
        with self.assertRaisesRegex(ValueError, "invalid_reviewer_worker_poll_interval"):
            ReviewerWorkerService(worker, heartbeat_callback=lambda _: None, poll_interval_seconds=0)


if __name__ == "__main__":
    unittest.main()
