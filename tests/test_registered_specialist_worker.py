import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock

from automation.worker_registry import WorkerRegistry, WorkerSessionError
from automation.registered_specialist_worker import RegisteredSpecialistWorker


class RegisteredSpecialistWorkerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.registry = WorkerRegistry(str(Path(self.tmp.name) / "workers.sqlite3"))
        self.worker = Mock()
        self.worker.worker_id = "specialist-1"

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
        self.assertEqual(records[0].metadata, {"role": "research"})
        self.assertEqual(records[0].status, "active")

    def test_run_once_starts_registry_before_delegating(self):
        self.worker.run_once.return_value = "task-result"
        registered = RegisteredSpecialistWorker(self.worker, self.registry)
        self.addCleanup(registered.close)
        self.assertEqual(registered.run_once(), "task-result")
        self.worker.run_once.assert_called_once_with()
        self.assertTrue(registered.health_snapshot()["registered"])

    def test_close_marks_stopped_and_is_idempotent(self):
        registered = RegisteredSpecialistWorker(self.worker, self.registry)
        registered.start()
        registered.close()
        registered.close()
        self.assertTrue(registered.health_snapshot()["closed"])
        self.assertEqual(self.registry.list_workers()[0].status, "stopped")

    def test_replaced_session_fails_closed_before_new_claims(self):
        registered = RegisteredSpecialistWorker(self.worker, self.registry)
        registered.start()
        self.registry.register("specialist-1")
        with self.assertRaises(RuntimeError):
            registered.run_once()
        self.worker.run_once.assert_not_called()
        registered._stop.set()
        if registered._thread:
            registered._thread.join(timeout=1)

    def test_invalid_heartbeat_interval_rejected(self):
        with self.assertRaises(ValueError):
            RegisteredSpecialistWorker(self.worker, self.registry, heartbeat_interval=0)


if __name__ == "__main__":
    unittest.main()
