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

    def test_grace_period_is_bounded_and_validated(self):
        coordinator = WorkerShutdownCoordinator()
        for invalid in (-1, 301, True, "1"):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    coordinator.wait_for_idle(invalid)
        self.assertTrue(coordinator.wait_for_idle(0))


if __name__ == "__main__":
    unittest.main()
