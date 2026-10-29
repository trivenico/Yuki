import unittest
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from datetime import datetime
from unittest.mock import Mock

from core.evolution_scheduler import EvolutionScheduler


class EvolutionSchedulerTests(unittest.TestCase):
    def setUp(self):
        self.cycle = Mock()
        self.cycle.status.return_value = {"last_report_at": None}

    def test_disabled_scheduler_never_runs_cycle(self):
        scheduler = EvolutionScheduler(self.cycle, enabled=False, hour=3)
        self.assertFalse(scheduler.run_if_due(datetime.fromisoformat("2026-10-28T12:00:00+00:00")))
        self.cycle.run_cycle.assert_not_called()

    def test_waits_until_configured_local_hour(self):
        scheduler = EvolutionScheduler(self.cycle, enabled=True, hour=3)
        self.assertFalse(scheduler.run_if_due(datetime.fromisoformat("2026-10-28T02:59:00+00:00")))
        self.cycle.run_cycle.assert_not_called()

    def test_runs_at_most_once_per_local_date(self):
        scheduler = EvolutionScheduler(self.cycle, enabled=True, hour=3)
        first = datetime.fromisoformat("2026-10-28T03:00:00+00:00")
        self.assertTrue(scheduler.run_if_due(first))
        self.cycle.run_cycle.assert_called_once_with(now=first)
        self.cycle.status.return_value = {"last_report_at": "2026-10-28T03:00:00+00:00"}
        self.assertFalse(scheduler.run_if_due(datetime.fromisoformat("2026-10-28T15:00:00+00:00")))
        self.cycle.run_cycle.assert_called_once()

    def test_daily_deduplication_uses_the_supplied_local_timezone(self):
        scheduler = EvolutionScheduler(self.cycle, enabled=True, hour=3)
        self.cycle.status.return_value = {"last_report_at": "2026-10-27T17:00:00+00:00"}
        local_now = datetime.fromisoformat("2026-10-28T03:00:00+10:00")
        self.assertFalse(scheduler.run_if_due(local_now))
        self.cycle.run_cycle.assert_not_called()


    def test_concurrent_due_checks_run_only_one_cycle(self):
        scheduler = EvolutionScheduler(self.cycle, enabled=True, hour=3)
        now = datetime.fromisoformat("2026-10-28T12:00:00+00:00")
        barrier = Barrier(2)

        def run_cycle(*, now):
            self.cycle.status.return_value = {"last_report_at": now.isoformat()}

        self.cycle.run_cycle.side_effect = run_cycle

        def attempt():
            barrier.wait()
            return scheduler.run_if_due(now)

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: attempt(), range(2)))

        self.assertEqual(results.count(True), 1)
        self.assertEqual(results.count(False), 1)
        self.cycle.run_cycle.assert_called_once_with(now=now)

    def test_naive_now_is_rejected(self):
        scheduler = EvolutionScheduler(self.cycle, enabled=True, hour=3)
        with self.assertRaisesRegex(ValueError, "timezone-aware"):
            scheduler.run_if_due(datetime(2026, 10, 28, 12))
        self.cycle.run_cycle.assert_not_called()

    def test_invalid_hour_and_poll_interval_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "hour"):
            EvolutionScheduler(self.cycle, enabled=True, hour=24)
        with self.assertRaisesRegex(ValueError, "poll_seconds"):
            EvolutionScheduler(self.cycle, enabled=True, poll_seconds=0)

    def test_invalid_existing_timestamp_fails_closed(self):
        scheduler = EvolutionScheduler(self.cycle, enabled=True, hour=3)
        self.cycle.status.return_value = {"last_report_at": "not-a-timestamp"}
        with self.assertRaisesRegex(RuntimeError, "timestamp is invalid"):
            scheduler.run_if_due(datetime.fromisoformat("2026-10-28T12:00:00+00:00"))
        self.cycle.run_cycle.assert_not_called()


if __name__ == "__main__":
    unittest.main()
