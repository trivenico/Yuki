import threading
import unittest
from unittest.mock import Mock

from automation.production_worker_runtime import ProductionWorkerRuntime
from scripts.run_worker import load_production_runtime, run_worker_service


class FakeSecurityRuntime:
    def __init__(self, order):
        self.order = order

    def close(self):
        self.order.append("security")


class WorkerServiceTests(unittest.TestCase):
    def make_runtime(self, order, stop_event=None, fail_readiness=False):
        runtime = ProductionWorkerRuntime(
            task_runtime=object(), worker=object(), supervisor=object(),
            workflow_store=object(), delegation_authority=object(),
            security_runtime=FakeSecurityRuntime(order),
        )
        runtime.cycles = 0
        runtime.closed = 0

        def verify():
            if fail_readiness:
                raise ConnectionError("database_unavailable")

        def cycle():
            runtime.cycles += 1
            if stop_event is not None:
                stop_event.set()

        def close():
            runtime.closed += 1
            order.append("worker")

        runtime.verify_readiness = verify
        runtime.run_cycle = cycle
        runtime.close = close
        return runtime

    def test_loader_rejects_uncomposed_runtime(self):
        with self.assertRaisesRegex(TypeError, "bootstrap_must_return"):
            load_production_runtime(lambda: object())

    def test_service_runs_finite_cycles_and_closes_in_ownership_order(self):
        order = []
        stop = threading.Event()
        runtime = self.make_runtime(order, stop)
        result = run_worker_service(runtime, stop, cycle_interval_seconds=0.1)
        self.assertEqual(result, 0)
        self.assertEqual(runtime.cycles, 1)
        self.assertEqual(runtime.closed, 1)
        self.assertEqual(order, ["worker", "security"])

    def test_readiness_failure_still_closes_worker_then_security(self):
        order = []
        runtime = self.make_runtime(order, fail_readiness=True)
        with self.assertRaisesRegex(ConnectionError, "database_unavailable"):
            run_worker_service(runtime, threading.Event(), cycle_interval_seconds=0.1)
        self.assertEqual(order, ["worker", "security"])

    def test_cycle_failure_closes_both_owned_runtimes(self):
        order = []
        runtime = self.make_runtime(order)
        runtime.run_cycle = Mock(side_effect=ConnectionError("cycle_failed"))
        with self.assertRaisesRegex(ConnectionError, "cycle_failed"):
            run_worker_service(runtime, threading.Event(), cycle_interval_seconds=0.1)
        self.assertEqual(order, ["worker", "security"])

    def test_interval_is_bounded(self):
        runtime = self.make_runtime([])
        with self.assertRaisesRegex(ValueError, "out_of_range"):
            run_worker_service(runtime, threading.Event(), cycle_interval_seconds=0)
        self.assertEqual(runtime.closed, 0)


if __name__ == "__main__":
    unittest.main()
