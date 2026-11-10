import threading
import unittest
from types import SimpleNamespace

from automation.inference_worker_supervisor import InferenceWorkerSupervisor


class FakeWorker:
    def __init__(self, results):
        self.results = list(results)
        self.calls = 0

    def run_once(self):
        self.calls += 1
        result = self.results.pop(0) if self.results else SimpleNamespace(
            claimed=False, status="idle"
        )
        if isinstance(result, BaseException):
            raise result
        return result


class InferenceWorkerSupervisorTests(unittest.TestCase):
    def test_processes_success_and_failure_then_stops(self):
        stop = threading.Event()
        worker = FakeWorker([
            SimpleNamespace(claimed=True, status="succeeded"),
            SimpleNamespace(claimed=True, status="failed"),
        ])

        def wait(_):
            stop.set()

        stats = InferenceWorkerSupervisor(
            worker, stop_event=stop, wait=wait
        ).run_forever()
        self.assertEqual((stats.claimed, stats.succeeded, stats.failed), (2, 1, 1))
        self.assertEqual(stats.stopped_reason, "stop_requested")

    def test_device_revocation_stops_without_retry(self):
        worker = FakeWorker([PermissionError("do not expose credential")])
        stats = InferenceWorkerSupervisor(worker, wait=lambda _: None).run_forever()
        self.assertEqual(stats.stopped_reason, "device_authentication_failed")
        self.assertEqual(stats.transient_errors, 0)
        self.assertEqual(worker.calls, 1)

    def test_transient_errors_use_capped_exponential_backoff(self):
        worker = FakeWorker([RuntimeError("transient")] * 3)
        delays = []
        stats = InferenceWorkerSupervisor(
            worker,
            max_consecutive_errors=3,
            error_backoff_initial=0.1,
            error_backoff_max=0.15,
            wait=delays.append,
        ).run_forever()
        self.assertEqual(stats.stopped_reason, "consecutive_error_limit")
        self.assertEqual(stats.transient_errors, 3)
        self.assertEqual(delays, [0.1, 0.15])

    def test_idle_worker_waits_and_honours_stop(self):
        stop = threading.Event()
        worker = FakeWorker([])

        def wait(_):
            stop.set()

        stats = InferenceWorkerSupervisor(
            worker, stop_event=stop, wait=wait
        ).run_forever()
        self.assertEqual(stats.idle_polls, 1)
        self.assertEqual(worker.calls, 1)

    def test_invalid_backoff_and_retry_bounds_are_rejected(self):
        with self.assertRaises(ValueError):
            InferenceWorkerSupervisor(
                FakeWorker([]), error_backoff_initial=2, error_backoff_max=1
            )
        with self.assertRaises(ValueError):
            InferenceWorkerSupervisor(FakeWorker([]), max_consecutive_errors=0)


if __name__ == "__main__":
    unittest.main()
