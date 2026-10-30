import tempfile
import unittest
from pathlib import Path

from automation.local_worker_runtime import (
    LOCAL_OPERATOR_ACTOR,
    WORKER_SERVICE_ACTOR,
    build_local_worker_runtime,
)


class FakeProvider:
    def is_available(self):
        return True

    def generate(self, prompt):
        return "bounded specialist output"


class LocalWorkerRuntimeTests(unittest.TestCase):
    def test_factory_shares_runtime_and_builds_all_specialists(self):
        with tempfile.TemporaryDirectory() as directory:
            built = build_local_worker_runtime(Path(directory), FakeProvider())
            self.assertIs(built.supervisor.runtime, built.runtime)
            self.assertEqual(built.operator_actor_id, LOCAL_OPERATOR_ACTOR)
            self.assertEqual(
                set(built.supervisor.worker.handlers),
                {"research", "coding", "testing", "reporting"},
            )
            self.assertTrue((Path(directory) / "yuki_tasks.sqlite3").exists())

    def test_operator_can_list_but_unknown_actor_is_denied(self):
        with tempfile.TemporaryDirectory() as directory:
            built = build_local_worker_runtime(directory, FakeProvider())
            self.assertEqual(
                built.runtime.list_task_summaries(actor_id=LOCAL_OPERATOR_ACTOR),
                [],
            )
            with self.assertRaises(PermissionError):
                built.runtime.list_task_summaries(actor_id="untrusted-user")

    def test_worker_service_is_not_granted_operator_listing(self):
        with tempfile.TemporaryDirectory() as directory:
            built = build_local_worker_runtime(directory, FakeProvider())
            with self.assertRaises(PermissionError):
                built.runtime.list_task_summaries(actor_id=WORKER_SERVICE_ACTOR)

    def test_cycle_limit_is_bounded(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                build_local_worker_runtime(
                    directory, FakeProvider(), max_tasks_per_cycle=11,
                )


if __name__ == "__main__":
    unittest.main()
