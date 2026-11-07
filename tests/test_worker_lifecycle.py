import threading
import time
import unittest

from automation.worker_lifecycle import WorkerLeaseLost, WorkerShutdownCoordinator


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

    def test_execute_claimed_releases_active_tracking_after_success(self):
        from types import SimpleNamespace

        coordinator = WorkerShutdownCoordinator()
        claimed = SimpleNamespace(run_id="task-success")
        self.assertTrue(coordinator.try_begin_claim("worker-a"))
        coordinator.register_claim("worker-a", claimed.run_id)
        result = coordinator.execute_claimed(
            "worker-a", claimed, lambda task: ("handled", task.run_id),
        )
        self.assertEqual(result, ("handled", "task-success"))
        self.assertEqual(coordinator.snapshot()["active_workers"], {})

    def test_execute_claimed_releases_tracking_after_handler_exception_without_retry(self):
        from types import SimpleNamespace

        coordinator = WorkerShutdownCoordinator()
        claimed = SimpleNamespace(run_id="task-error")
        calls = []
        self.assertTrue(coordinator.try_begin_claim("worker-a"))
        coordinator.register_claim("worker-a", claimed.run_id)

        def handler(task):
            calls.append(task.run_id)
            raise OSError("handler failed")

        with self.assertRaises(OSError):
            coordinator.execute_claimed("worker-a", claimed, handler)
        self.assertEqual(calls, ["task-error"])
        self.assertEqual(coordinator.snapshot()["active_workers"], {})

    def test_execute_claimed_requires_matching_registered_ownership(self):
        from types import SimpleNamespace

        coordinator = WorkerShutdownCoordinator()
        called = []
        with self.assertRaises(RuntimeError):
            coordinator.execute_claimed(
                "worker-a", SimpleNamespace(run_id="task-unclaimed"),
                lambda _task: called.append(True),
            )
        self.assertEqual(called, [])

    def test_heartbeat_renews_lease_while_handler_runs(self):
        from types import SimpleNamespace

        coordinator = WorkerShutdownCoordinator()
        claimed = SimpleNamespace(run_id="task-heartbeat")
        self.assertTrue(coordinator.try_begin_claim("worker-a"))
        coordinator.register_claim("worker-a", claimed.run_id)
        renewed = threading.Event()

        def renew():
            renewed.set()
            return True

        def handler(_task, lease_lost):
            self.assertFalse(lease_lost.wait(0.08))
            self.assertTrue(renewed.is_set())
            return "done"

        result = coordinator.execute_claimed_with_heartbeat(
            "worker-a", claimed, handler, renew, heartbeat_interval=0.01,
        )
        self.assertEqual(result, "done")
        self.assertEqual(coordinator.snapshot()["active_workers"], {})

    def test_heartbeat_failure_signals_handler_and_rejects_result(self):
        from types import SimpleNamespace

        coordinator = WorkerShutdownCoordinator()
        claimed = SimpleNamespace(run_id="task-lost-lease")
        self.assertTrue(coordinator.try_begin_claim("worker-a"))
        coordinator.register_claim("worker-a", claimed.run_id)
        observed = threading.Event()

        def handler(_task, lease_lost):
            self.assertTrue(lease_lost.wait(1.0))
            observed.set()
            return "must-not-be-accepted"

        with self.assertRaises(WorkerLeaseLost):
            coordinator.execute_claimed_with_heartbeat(
                "worker-a", claimed, handler, lambda: False,
                heartbeat_interval=0.01,
            )
        self.assertTrue(observed.is_set())
        self.assertEqual(coordinator.snapshot()["active_workers"], {})

    def test_inflight_heartbeat_failure_after_handler_return_rejects_result(self):
        from types import SimpleNamespace

        coordinator = WorkerShutdownCoordinator()
        claimed = SimpleNamespace(run_id="task-heartbeat-race")
        self.assertTrue(coordinator.try_begin_claim("worker-a"))
        coordinator.register_claim("worker-a", claimed.run_id)
        renewal_started = threading.Event()
        release_renewal = threading.Event()
        outcomes = []

        def renew():
            renewal_started.set()
            release_renewal.wait(1.0)
            return False

        def handler(_task, _lease_lost):
            self.assertTrue(renewal_started.wait(1.0))
            return "must-not-escape"

        def execute():
            try:
                outcomes.append(coordinator.execute_claimed_with_heartbeat(
                    "worker-a", claimed, handler, renew, heartbeat_interval=0.01,
                ))
            except BaseException as exc:
                outcomes.append(exc)

        thread = threading.Thread(target=execute)
        thread.start()
        self.assertTrue(renewal_started.wait(1.0))
        release_renewal.set()
        thread.join(1.0)
        self.assertFalse(thread.is_alive())
        self.assertEqual(len(outcomes), 1)
        self.assertIsInstance(outcomes[0], WorkerLeaseLost)
        self.assertEqual(coordinator.snapshot()["active_workers"], {})

    def test_stuck_heartbeat_times_out_without_releasing_active_ownership(self):
        from types import SimpleNamespace

        coordinator = WorkerShutdownCoordinator()
        claimed = SimpleNamespace(run_id="task-stuck-heartbeat")
        self.assertTrue(coordinator.try_begin_claim("worker-a"))
        coordinator.register_claim("worker-a", claimed.run_id)
        renewal_started = threading.Event()
        release_renewal = threading.Event()

        def renew():
            renewal_started.set()
            release_renewal.wait(1.0)
            return True

        def handler(_task, _lease_lost):
            self.assertTrue(renewal_started.wait(1.0))
            return "must-not-be-accepted"

        started = time.monotonic()
        with self.assertRaises(WorkerLeaseLost):
            coordinator.execute_claimed_with_heartbeat(
                "worker-a", claimed, handler, renew,
                heartbeat_interval=0.01, heartbeat_timeout=0.02,
            )
        self.assertLess(time.monotonic() - started, 0.5)
        self.assertEqual(
            coordinator.snapshot()["active_workers"], {"worker-a": "task-stuck-heartbeat"},
        )
        release_renewal.set()
        deadline = time.monotonic() + 1.0
        while coordinator.snapshot()["active_workers"] and time.monotonic() < deadline:
            time.sleep(0.001)
        self.assertEqual(coordinator.snapshot()["active_workers"], {})

    def test_heartbeat_timeout_validation(self):
        from types import SimpleNamespace

        coordinator = WorkerShutdownCoordinator()
        claimed = SimpleNamespace(run_id="task-heartbeat-timeout-validation")
        self.assertTrue(coordinator.try_begin_claim("worker-a"))
        coordinator.register_claim("worker-a", claimed.run_id)
        for invalid in (0, 61, True, "1"):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    coordinator.execute_claimed_with_heartbeat(
                        "worker-a", claimed, lambda *_: None, lambda: True,
                        heartbeat_timeout=invalid,
                    )
        coordinator.finish_task("worker-a", claimed.run_id)

    def test_heartbeat_requires_registered_ownership_and_valid_interval(self):
        from types import SimpleNamespace

        coordinator = WorkerShutdownCoordinator()
        claimed = SimpleNamespace(run_id="task-not-owned")
        with self.assertRaises(RuntimeError):
            coordinator.execute_claimed_with_heartbeat(
                "worker-a", claimed, lambda *_: None, lambda: True,
            )
        self.assertTrue(coordinator.try_begin_claim("worker-a"))
        coordinator.register_claim("worker-a", claimed.run_id)
        with self.assertRaises(ValueError):
            coordinator.execute_claimed_with_heartbeat(
                "worker-a", claimed, lambda *_: None, lambda: True,
                heartbeat_interval=0,
            )
        coordinator.finish_task("worker-a", claimed.run_id)

    def test_grace_period_is_bounded_and_validated(self):
        coordinator = WorkerShutdownCoordinator()
        for invalid in (-1, 301, True, "1"):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    coordinator.wait_for_idle(invalid)
        self.assertTrue(coordinator.wait_for_idle(0))


if __name__ == "__main__":
    unittest.main()
