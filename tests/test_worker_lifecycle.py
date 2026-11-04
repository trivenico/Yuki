import threading
import time
import unittest

from automation.worker_lifecycle import WorkerShutdownCoordinator


class WorkerShutdownCoordinatorTests(unittest.TestCase):
    def test_shutdown_stops_new_claims_and_waits_for_active_task(self):
        coordinator = WorkerShutdownCoordinator()
        self.assertTrue(coordinator.try_begin_claim("worker-a"))
        coordinator.register_claim("worker-a", "task-1")
        outcome = []
        waiter = threading.Thread(target=lambda: outcome.append(coordinator.wait_for_idle(1.0)))
        waiter.start()
        deadline = time.monotonic() + 0.5
        while not coordinator.stopping and time.monotonic() < deadline:
            time.sleep(0.001)
        self.assertTrue(coordinator.stopping)
        self.assertFalse(coordinator.try_begin_claim("worker-b"))
        coordinator.finish_task("worker-a", "task-1")
        waiter.join(1.0)
        self.assertFalse(waiter.is_alive())
        self.assertEqual(outcome, [True])

    def test_grace_timeout_preserves_active_task_for_lease_recovery(self):
        coordinator = WorkerShutdownCoordinator()
        self.assertTrue(coordinator.try_begin_claim("worker-a"))
        coordinator.register_claim("worker-a", "task-1")
        self.assertFalse(coordinator.wait_for_idle(0))
        snapshot = coordinator.snapshot()
        self.assertTrue(snapshot["stopping"])
        self.assertEqual(snapshot["active_workers"], {"worker-a": "task-1"})
        self.assertFalse(coordinator.try_begin_claim("worker-b"))

    def test_claim_reservation_is_atomic_with_shutdown_gate(self):
        coordinator = WorkerShutdownCoordinator()
        self.assertTrue(coordinator.try_begin_claim("worker-a"))
        coordinator.begin_shutdown()
        self.assertFalse(coordinator.try_begin_claim("worker-b"))
        coordinator.release_claim_reservation("worker-a")
        self.assertTrue(coordinator.wait_for_idle(0))

    def test_rejects_duplicate_and_mismatched_worker_ownership(self):
        coordinator = WorkerShutdownCoordinator()
        self.assertTrue(coordinator.try_begin_claim("worker-a"))
        coordinator.register_claim("worker-a", "task-1")
        with self.assertRaises(RuntimeError):
            coordinator.try_begin_claim("worker-a")
        with self.assertRaises(RuntimeError):
            coordinator.finish_task("worker-b", "task-1")
        with self.assertRaises(RuntimeError):
            coordinator.register_claim("worker-b", "task-1")
        coordinator.finish_task("worker-a", "task-1")

    def test_claim_next_integrates_shutdown_gate_with_durable_store(self):
        from types import SimpleNamespace

        class Store:
            def __init__(self):
                self.calls = 0

            def claim_next(self, *, worker_id, lease_seconds):
                self.calls += 1
                self.asserted = (worker_id, lease_seconds)
                return SimpleNamespace(run_id="task-42")

        coordinator = WorkerShutdownCoordinator()
        store = Store()
        claimed = coordinator.claim_next(store, "worker-a", lease_seconds=90)
        self.assertEqual(claimed.run_id, "task-42")
        self.assertEqual(store.asserted, ("worker-a", 90))
        self.assertEqual(
            coordinator.snapshot()["active_workers"], {"worker-a": "task-42"},
        )
        coordinator.begin_shutdown()
        self.assertIsNone(coordinator.claim_next(store, "worker-b"))
        self.assertEqual(store.calls, 1)

    def test_claim_next_releases_reservation_on_empty_queue_or_store_error(self):
        class EmptyStore:
            def claim_next(self, **_kwargs):
                return None

        class BrokenStore:
            def claim_next(self, **_kwargs):
                raise OSError("database unavailable")

        coordinator = WorkerShutdownCoordinator()
        self.assertIsNone(coordinator.claim_next(EmptyStore(), "worker-a"))
        self.assertEqual(coordinator.snapshot()["claim_reservations"], 0)
        with self.assertRaises(OSError):
            coordinator.claim_next(BrokenStore(), "worker-b")
        self.assertEqual(coordinator.snapshot()["claim_reservations"], 0)
        self.assertTrue(coordinator.try_begin_claim("worker-b"))
        coordinator.release_claim_reservation("worker-b")

    def test_grace_period_is_bounded_and_validated(self):
        coordinator = WorkerShutdownCoordinator()
        for invalid in (-1, 301, True, "1"):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    coordinator.wait_for_idle(invalid)
        self.assertTrue(coordinator.wait_for_idle(0))


if __name__ == "__main__":
    unittest.main()
