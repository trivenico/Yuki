import unittest
from types import SimpleNamespace

from ui.task_controls import execute_worker_cycle


class FakeSupervisor:
    def __init__(self):
        self.calls = 0

    def run_cycle(self):
        self.calls += 1
        return SimpleNamespace(
            recovered_task_ids=("task-expired",),
            processed_count=2,
            succeeded_count=1,
            failed_count=1,
            idle=False,
        )


class WorkerDashboardControlTests(unittest.TestCase):
    def setUp(self):
        self.supervisor = FakeSupervisor()
        self.grants = set()

    def authorize(self, actor_id, action):
        return actor_id == "operator-1" and action in self.grants

    def test_requires_exact_confirmation(self):
        with self.assertRaises(ValueError):
            execute_worker_cycle(
                self.supervisor, actor_id="operator-1",
                authorize=self.authorize, confirmation="run",
            )
        self.assertEqual(self.supervisor.calls, 0)

    def test_denied_operator_cannot_start_worker(self):
        with self.assertRaises(PermissionError):
            execute_worker_cycle(
                self.supervisor, actor_id="operator-1",
                authorize=self.authorize, confirmation="RUN",
            )
        self.assertEqual(self.supervisor.calls, 0)

    def test_authorized_operator_runs_one_bounded_cycle(self):
        self.grants.add("worker:run")
        result = execute_worker_cycle(
            self.supervisor, actor_id="operator-1",
            authorize=self.authorize, confirmation="RUN",
        )
        self.assertIn("processed=2", result)
        self.assertIn("succeeded=1", result)
        self.assertIn("failed=1", result)
        self.assertIn("recovered=1", result)
        self.assertEqual(self.supervisor.calls, 1)

    def test_missing_identity_or_supervisor_fails_closed(self):
        self.grants.add("worker:run")
        with self.assertRaises(ValueError):
            execute_worker_cycle(
                self.supervisor, actor_id="",
                authorize=self.authorize, confirmation="RUN",
            )
        with self.assertRaises(TypeError):
            execute_worker_cycle(
                None, actor_id="operator-1",
                authorize=self.authorize, confirmation="RUN",
            )
        self.assertEqual(self.supervisor.calls, 0)

    def test_authorizer_exception_fails_closed(self):
        def broken_authorizer(*_args):
            raise RuntimeError("authorization backend unavailable")

        with self.assertRaises(PermissionError):
            execute_worker_cycle(
                self.supervisor, actor_id="operator-1",
                authorize=broken_authorizer, confirmation="RUN",
            )
        self.assertEqual(self.supervisor.calls, 0)


if __name__ == "__main__":
    unittest.main()
