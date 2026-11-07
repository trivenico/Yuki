"""Tests for the supervised reviewer recovery polling service."""
from __future__ import annotations

import unittest
from unittest.mock import Mock

from automation.reviewer_worker_recovery_service import (
    ReviewerWorkerRecoveryService, ReviewerRecoveryServiceError,
)


class ReviewerWorkerRecoveryServiceTests(unittest.TestCase):
    def test_bounded_run_emits_running_and_stopped_heartbeats(self):
        supervisor = Mock()
        supervisor.run_once.return_value = Mock(recovered_tasks=2, deferred_tasks=1)
        heartbeats = []
        service = ReviewerWorkerRecoveryService(
            supervisor, heartbeat_callback=heartbeats.append,
            poll_interval_seconds=0.1, batch_limit=7,
        )
        report = service.run_forever(max_cycles=2)
        self.assertEqual(report.cycles, 2)
        self.assertEqual(report.recovered_tasks, 4)
        self.assertEqual(report.deferred_tasks, 2)
        self.assertTrue(report.stopped)
        self.assertEqual([h.state for h in heartbeats], ["running", "running", "running", "stopped"])
        self.assertEqual(supervisor.run_once.call_count, 2)
        self.assertEqual(supervisor.run_once.call_args.kwargs, {"limit": 7})

    def test_cycle_failure_is_isolated_and_reported(self):
        supervisor = Mock()
        supervisor.run_once.side_effect = [RuntimeError("private detail"), Mock(recovered_tasks=1, deferred_tasks=0)]
        heartbeats = []
        service = ReviewerWorkerRecoveryService(
            supervisor, heartbeat_callback=heartbeats.append,
            poll_interval_seconds=0.1,
        )
        report = service.run_forever(max_cycles=2)
        self.assertEqual(report.failed_cycles, 1)
        self.assertEqual(report.recovered_tasks, 1)
        self.assertEqual(heartbeats[1].state, "degraded")
        self.assertEqual(heartbeats[1].last_error_code, "recovery_cycle.RuntimeError")

    def test_reentrant_service_run_is_rejected(self):
        supervisor = Mock()
        heartbeats = []
        service = ReviewerWorkerRecoveryService(
            supervisor, heartbeat_callback=heartbeats.append,
        )
        service._running = True
        with self.assertRaisesRegex(ReviewerRecoveryServiceError, "already_running"):
            service.run_forever(max_cycles=1)

    def test_invalid_bounds_fail_closed(self):
        supervisor = Mock()
        with self.assertRaises(ValueError):
            ReviewerWorkerRecoveryService(
                supervisor, heartbeat_callback=lambda _: None, batch_limit=0,
            )


if __name__ == "__main__":
    unittest.main()
