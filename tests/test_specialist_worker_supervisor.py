import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from automation.registered_specialist_worker import RegisteredSpecialistWorker
from automation.specialist_worker import SpecialistTaskWorker
from automation.specialist_worker_supervisor import SpecialistWorkerSupervisor
from automation.worker_registry import WorkerRegistry


class SpecialistWorkerSupervisorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.registry = WorkerRegistry(str(Path(self.tmp.name) / "workers.sqlite3"))

    def make_registered(self, worker_id):
        worker = object.__new__(SpecialistTaskWorker)
        worker.worker_id = worker_id
        worker.run_once = Mock(return_value=None)
        return RegisteredSpecialistWorker(worker, self.registry)

    def test_supervisor_registers_workers_and_closes_sessions_on_shutdown(self):
        first = self.make_registered("specialist-a")
        second = self.make_registered("specialist-b")
        supervisor = SpecialistWorkerSupervisor(
            [first, second], idle_sleep_seconds=0.01,
        )
        first.worker.run_once.side_effect = lambda: supervisor.request_shutdown()
        supervisor.run_until_shutdown()
        self.assertTrue(first.health_snapshot()["closed"])
        self.assertTrue(second.health_snapshot()["closed"])
        records = {item.worker_id: item.status for item in self.registry.list_workers()}
        self.assertEqual(records, {"specialist-a": "stopped", "specialist-b": "stopped"})

    def test_duplicate_worker_ids_rejected(self):
        first = self.make_registered("same-worker")
        second = self.make_registered("same-worker")
        with self.assertRaises(ValueError):
            SpecialistWorkerSupervisor([first, second])

    def test_supervisor_can_be_closed_before_run(self):
        worker = self.make_registered("specialist-a")
        supervisor = SpecialistWorkerSupervisor([worker])
        supervisor.close()
        with self.assertRaises(RuntimeError):
            supervisor.run_until_shutdown()
        self.assertTrue(worker.health_snapshot()["closed"])


if __name__ == "__main__":
    unittest.main()
