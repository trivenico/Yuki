import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from automation.specialist_worker import SpecialistTaskWorker
from automation.worker_registry import WorkerRegistry
from automation.registered_specialist_worker import RegisteredSpecialistWorker


def make_worker():
    worker = object.__new__(SpecialistTaskWorker)
    worker.worker_id = "specialist-1"
    worker.run_once = Mock(return_value="task-result")
    worker.run_concurrently = Mock(return_value=("batch-result",))
    return worker


class RegisteredSpecialistWorkerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.registry = WorkerRegistry(str(Path(self.tmp.name) / "workers.sqlite3"))
        self.worker = make_worker()

    def test_start_registers_and_heartbeat_updates_health(self):
        registered = RegisteredSpecialistWorker(
            self.worker, self.registry, heartbeat_interval=0.1,
            metadata={"role": "research"},
        )
        self.addCleanup(registered.close)
        registered.start()
        time.sleep(0.15)
        health = registered.health_snapshot()
        self.assertTrue(health["registered"])
        self.assertIsNotNone(health["last_registry_heartbeat"])
        records = self.registry.list_workers()
        self.assertEqual(records[0].metadata, {"role": "research", "capabilities": [], "worker_type": "specialist"})
        self.assertEqual(records[0].status, "active")

    def test_registry_advertises_only_explicit_handler_capability_grants(self):
        self.worker.handler_capabilities = {
            "research-agent": frozenset({"research", "summarize"}),
            "review-agent": frozenset({"code-review"}),
        }
        registered = RegisteredSpecialistWorker(self.worker, self.registry)
        self.addCleanup(registered.close)
        registered.start()
        metadata = self.registry.list_workers()[0].metadata
        self.assertEqual(metadata["capabilities"], ["code-review", "research", "summarize"])
        self.assertEqual(metadata["worker_type"], "specialist")

    def test_worker_metadata_cannot_overstate_handler_capability_grants(self):
        self.worker.handler_capabilities = {"research-agent": frozenset({"research"})}
        registered = RegisteredSpecialistWorker(
            self.worker, self.registry, metadata={"capabilities": ["browser"]},
        )
        with self.assertRaisesRegex(ValueError, "must match configured handler grants"):
            registered.start()
        self.assertEqual(self.registry.list_workers(), [])

    def test_run_once_starts_registry_before_delegating(self):
        registered = RegisteredSpecialistWorker(self.worker, self.registry)
        self.addCleanup(registered.close)
        self.assertEqual(registered.run_once(), "task-result")
        self.worker.run_once.assert_called_once_with()
        self.assertTrue(registered.health_snapshot()["registered"])

    def test_completed_background_task_is_forwarded_to_learning_observer(self):
        from types import SimpleNamespace

        task = SimpleNamespace(state="completed", owner_id="owner-a")
        self.worker.run_once.return_value = task
        observed = []
        registered = RegisteredSpecialistWorker(
            self.worker, self.registry, learning_observer=observed.append,
        )
        self.addCleanup(registered.close)
        self.assertIs(registered.run_once(), task)
        self.assertEqual(observed, [task])

    def test_learning_observer_failure_does_not_change_committed_task(self):
        from types import SimpleNamespace

        task = SimpleNamespace(state="completed", owner_id="owner-a")
        self.worker.run_once.return_value = task

        def broken_observer(_task):
            raise OSError("private memory backend detail")

        registered = RegisteredSpecialistWorker(
            self.worker, self.registry, learning_observer=broken_observer,
        )
        self.addCleanup(registered.close)
        self.assertIs(registered.run_once(), task)
        self.assertEqual(
            registered.health_snapshot()["learning_observer_error"],
            "learning_observer_failed",
        )

    def test_concurrent_background_results_are_forwarded_after_completion(self):
        from types import SimpleNamespace

        tasks = (
            SimpleNamespace(state="completed", owner_id="owner-a"),
            SimpleNamespace(state="failed", owner_id="owner-a"),
        )
        self.worker.run_concurrently.return_value = tasks
        observed = []
        registered = RegisteredSpecialistWorker(
            self.worker, self.registry, learning_observer=observed.append,
        )
        self.addCleanup(registered.close)
        self.assertEqual(registered.run_concurrently(), tasks)
        self.assertEqual(observed, [tasks[0]])

    def test_close_marks_stopped_and_is_idempotent(self):
        registered = RegisteredSpecialistWorker(self.worker, self.registry)
        registered.start()
        registered.close()
        registered.close()
        self.assertTrue(registered.health_snapshot()["closed"])
        self.assertEqual(self.registry.list_workers()[0].status, "stopped")

    def test_replaced_session_fails_closed_before_new_claims(self):
        registered = RegisteredSpecialistWorker(
            self.worker, self.registry, heartbeat_interval=0.1,
        )
        registered.start()
        self.registry.register("specialist-1")
        time.sleep(0.15)
        with self.assertRaises(RuntimeError):
            registered.run_once()
        self.worker.run_once.assert_not_called()
        registered.close()
        self.assertEqual(self.registry.list_workers()[0].status, "active")

    def test_heartbeat_thread_start_failure_closes_registered_session(self):
        class BrokenThread:
            def __init__(self, **kwargs):
                pass
            def start(self):
                raise RuntimeError("thread start failed")

        registered = RegisteredSpecialistWorker(self.worker, self.registry)
        with patch("automation.registered_specialist_worker.Thread", BrokenThread):
            with self.assertRaisesRegex(RuntimeError, "thread start failed"):
                registered.start()
        self.assertIsNone(self.worker._registry_session)
        self.assertEqual(self.registry.list_workers()[0].status, "stopped")

    def test_shutdown_timeout_keeps_worker_fenced_and_can_be_retried(self):
        class ControlledThread:
            def __init__(self, **_kwargs):
                self.alive = False
                self.join_calls = 0
            def start(self):
                self.alive = True
            def join(self, timeout=None):
                self.join_calls += 1
                if self.join_calls >= 2:
                    self.alive = False
            def is_alive(self):
                return self.alive

        registered = RegisteredSpecialistWorker(self.worker, self.registry)
        with patch("automation.registered_specialist_worker.Thread", ControlledThread):
            registered.start()
            with self.assertRaisesRegex(RuntimeError, "heartbeat did not stop"):
                registered.close(join_timeout=0)
            self.assertTrue(registered.health_snapshot()["closed"])
            with self.assertRaisesRegex(RuntimeError, "closed"):
                registered.run_once()
            registered.close(join_timeout=0)

        self.assertTrue(registered.health_snapshot()["closed"])
        self.assertIsNone(self.worker._registry_session)
        self.assertEqual(self.registry.list_workers()[0].status, "stopped")

    def test_invalid_heartbeat_interval_rejected(self):
        with self.assertRaises(ValueError):
            RegisteredSpecialistWorker(self.worker, self.registry, heartbeat_interval=0)


if __name__ == "__main__":
    unittest.main()
