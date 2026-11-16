import threading
import unittest

from automation.sandbox_execution_runtime import SandboxExecutionRuntime
from scripts.run_sandbox_worker import _poll_interval, run_worker


class _Engine:
    def __init__(self, recovered=0):
        self.recovered = recovered
        self.recovery_calls = 0

    def recover_expired_leases(self):
        self.recovery_calls += 1
        return self.recovered


class _Worker:
    def __init__(self, event, results):
        self.event = event
        self.results = list(results)
        self.calls = 0

    def run_once(self, *, lease_seconds):
        assert lease_seconds == 180
        self.calls += 1
        result = self.results.pop(0) if self.results else None
        if result is not None:
            self.event.set()
        return result


class _StopAfterWait(threading.Event):
    def wait(self, timeout=None):
        assert timeout is not None
        self.set()
        return True


def _runtime(engine, worker):
    runtime = SandboxExecutionRuntime.__new__(SandboxExecutionRuntime)
    runtime.engine = engine
    runtime.worker = worker
    return runtime


def test_worker_process_recovers_expired_leases_and_polls_until_stop():
    event = _StopAfterWait()
    engine = _Engine(recovered=2)
    worker = _Worker(event, [])
    run_worker(_runtime(engine, worker), stop_event=event, poll_interval=0.1)
    assert engine.recovery_calls == 1
    assert worker.calls == 1


def test_worker_process_finishes_current_task_before_stopping():
    event = threading.Event()
    engine = _Engine()
    worker = _Worker(event, [{"state": "succeeded"}])
    run_worker(_runtime(engine, worker), stop_event=event, poll_interval=0.1)
    assert worker.calls == 1
    assert engine.recovery_calls == 1


def test_worker_poll_interval_rejects_invalid_values():
    for value in (True, False, 0, 31, "1", None):
        with unittest.TestCase().subTest(value=value):
            with unittest.TestCase().assertRaises(ValueError):
                _poll_interval({"sandbox_execution_poll_interval_seconds": value})

def test_worker_poll_interval_accepts_bounded_numeric_value():
    assert _poll_interval({"sandbox_execution_poll_interval_seconds": 2}) == 2.0
