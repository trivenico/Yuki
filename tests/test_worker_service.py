import threading
import unittest
from unittest.mock import Mock

from scripts.run_worker import load_production_runtime, run_worker_service


class FakeSecurityRuntime:
    def __init__(self, order):
        self.order = order

    def close(self):
        self.order.append("security")


class FakeWorkerRuntime:
    def __init__(self, order, stop_event=None, fail_readiness=False):
        self.order = order
        self.security_runtime = FakeSecurityRuntime(order)
        self.stop_event = stop_event
        self.fail_readiness = fail_readiness
        self.cycles = 0
        self.closed = 0

    def verify_readiness(self):
        if self.fail_readiness:
            raise ConnectionError("database_unavailable")

    def run_cycle(self):
        self.cycles += 1
        if self.stop_event is not None:
            self.stop_event.set()

    def close(self):
        self.closed += 1
        self.order.append("worker")


class WorkerServiceTests(unittest.TestCase):
    def test_loader_rejects_untrusted_or_uncomposed_runtime(self):
        with self.assertRaisesRegex(TypeError, "production_worker_runtime_required|bootstrap_must_return"):
            load_production_runtime(lambda: object())

    def test_service_runs_finite_cycles_and_closes_in_ownership_order(self):
        order = []
        stop = threading.Event()
        runtime = FakeWorkerRuntime(order, stop)
        result = run_worker_service(runtime, stop, cycle_interval_seconds=0.1)
        self.assertEqual(result, 0)
        self.assertEqual(runtime.cycles, 1)
        self.assertEqual(runtime.closed, 1)
        self.assertEqual(order, ["worker", "security"])

    def test_readiness_failure_still_closes_worker_then_security(self):
        order = []
        runtime = FakeWorkerRuntime(order, fail_readiness=True)
        with self.assertRaisesRegex(ConnectionError, "database_unavailable"):
            run_worker_service(runtime, threading.Event(), cycle_interval_seconds=0.1)
        self.assertEqual(order, ["worker", "security"])

    def test_cycle_failure_closes_both_owned_runtimes(self):
        order = []
        runtime = FakeWorkerRuntime(order)
        runtime.run_cycle = Mock(side_effect=ConnectionError("cycle_failed"))
        with self.assertRaisesRegex(ConnectionError, "cycle_failed"):
            run_worker_service(runtime, threading.Event(), cycle_interval_seconds=0.1)
        self.assertEqual(order, ["worker", "security"])

    def test_interval_is_bounded(self):
        runtime = FakeWorkerRuntime([])
        with self.assertRaisesRegex(ValueError, "out_of_range"):
            run_worker_service(runtime, threading.Event(), cycle_interval_seconds=0)
        self.assertEqual(runtime.closed, 0)


if __name__ == "__main__":
    unittest.main()
