import tempfile
import unittest
from pathlib import Path

from automation.agent_coordinator import SubtaskSpec

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

    def test_operator_approval_is_allowed_and_session_is_ephemeral(self):
        with tempfile.TemporaryDirectory() as directory:
            first = build_local_worker_runtime(directory, FakeProvider())
            second = build_local_worker_runtime(directory, FakeProvider())
            self.assertTrue(first.runtime._allowed(
                LOCAL_OPERATOR_ACTOR, "task:approve", "task-1",
            ))
            self.assertNotEqual(first.approval_session_id, second.approval_session_id)
            self.assertGreaterEqual(len(first.approval_session_id), 32)

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

    def test_coordinator_dispatches_to_matching_provider_specialist_but_requires_approval(self):
        with tempfile.TemporaryDirectory() as directory:
            built = build_local_worker_runtime(directory, FakeProvider())
            plan = built.coordinator.submit(
                actor_id=LOCAL_OPERATOR_ACTOR,
                idempotency_key="local-coordinator-test",
                subtasks=[
                    SubtaskSpec(
                        subtask_id="research-brief",
                        description="Summarize the supplied requirements",
                        required_capabilities=("research",),
                    )
                ],
            )
            plan = built.coordinator.advance(
                actor_id=LOCAL_OPERATOR_ACTOR, plan_id=plan.plan_id,
            )
            subtask = plan.subtasks[0]
            self.assertEqual(subtask.agent_id, "research")
            self.assertIsNotNone(subtask.task_id)
            task = built.runtime.get(
                actor_id=LOCAL_OPERATOR_ACTOR, task_id=subtask.task_id,
            )
            self.assertEqual(task.state, "queued")
            self.assertIsNone(built.supervisor.worker.run_once())
            self.assertEqual(
                built.runtime.get(
                    actor_id=LOCAL_OPERATOR_ACTOR, task_id=subtask.task_id,
                ).state,
                "queued",
            )

    def test_cycle_limit_is_bounded(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                build_local_worker_runtime(
                    directory, FakeProvider(), max_tasks_per_cycle=11,
                )


if __name__ == "__main__":
    unittest.main()
