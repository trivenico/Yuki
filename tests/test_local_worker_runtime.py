import tempfile
import unittest
from pathlib import Path

from automation.agent_coordinator import SubtaskSpec
from core.planner import Planner

from automation.local_worker_runtime import (
    LOCAL_OPERATOR_ACTOR,
    WORKER_SERVICE_ACTOR,
    build_local_worker_runtime,
    build_session_worker_runtime,
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
            self.assertIs(built.service.supervisor, built.supervisor)
            self.assertEqual(built.service.status.state, "stopped")
            self.assertEqual(built.operator_actor_id, LOCAL_OPERATOR_ACTOR)
            self.assertEqual(
                set(built.supervisor.worker.handlers),
                {"research", "coding", "testing", "reporting"},
            )
            self.assertTrue((Path(directory) / "yuki_tasks.sqlite3").exists())

    def test_local_runtime_accepts_per_specialist_provider_preferences(self):
        class RoutedManager:
            def __init__(self):
                self.selected = FakeProvider()
                self.calls = []
            def create_selected_adapter(self):
                return self.selected
            def create_routed_adapter(self, *, required, preference, allow_cloud):
                self.calls.append((required, preference, allow_cloud))
                return FakeProvider()

        manager = RoutedManager()
        with tempfile.TemporaryDirectory() as directory:
            built = build_local_worker_runtime(
                directory,
                provider_manager=manager,
                provider_preferences={
                    "research": ("ollama",),
                    "coding": ("openai",),
                },
                allow_cloud=True,
            )
            self.assertEqual(
                set(built.supervisor.worker.handlers),
                {"research", "coding", "testing", "reporting"},
            )
            self.assertEqual(manager.calls, [
                (("text",), ("ollama",), True),
                (("text",), ("openai",), True),
            ])

    def test_local_runtime_requires_cloud_opt_in_for_selected_cloud_provider(self):
        from yuki_providers import ProviderManager
        manager = ProviderManager(environ={"OPENAI_API_KEY": "test-only-key"})
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(LookupError):
                build_local_worker_runtime(directory, provider_manager=manager)
            built = build_local_worker_runtime(
                directory, provider_manager=manager, allow_cloud=True,
            )
            self.assertEqual(
                set(built.supervisor.worker.handlers),
                {"research", "coding", "testing", "reporting"},
            )

    def test_local_runtime_rejects_ambiguous_provider_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                build_local_worker_runtime(directory)
            with self.assertRaises(ValueError):
                build_local_worker_runtime(
                    directory, FakeProvider(), provider_manager=object(),
                )
            with self.assertRaises(ValueError):
                build_local_worker_runtime(
                    directory, FakeProvider(), provider_preferences={"research": ("ollama",)},
                )

    def test_operator_approval_is_allowed_and_session_is_ephemeral(self):
        with tempfile.TemporaryDirectory() as directory:
            first = build_local_worker_runtime(directory, FakeProvider())
            second = build_local_worker_runtime(directory, FakeProvider())
            self.assertTrue(first.runtime._allowed(
                LOCAL_OPERATOR_ACTOR, "task:approve", "task-1",
            ))
            self.assertNotEqual(first.approval_session_id, second.approval_session_id)
            self.assertGreaterEqual(len(first.approval_session_id), 32)

    def test_task_approval_requires_exact_ephemeral_local_session(self):
        with tempfile.TemporaryDirectory() as directory:
            built = build_local_worker_runtime(directory, FakeProvider())
            plan = built.coordinator.submit(
                actor_id=LOCAL_OPERATOR_ACTOR,
                idempotency_key="local-session-approval",
                subtasks=[SubtaskSpec("research", "Collect public sources", ("research",))],
            )
            plan = built.coordinator.advance(actor_id=LOCAL_OPERATOR_ACTOR, plan_id=plan.plan_id)
            task_id = plan.subtasks[0].task_id
            task = built.runtime.get(actor_id=LOCAL_OPERATOR_ACTOR, task_id=task_id)
            with self.assertRaises(PermissionError):
                built.runtime.approve(actor_id=LOCAL_OPERATOR_ACTOR, task_id=task_id,
                                      session_id="caller-forged-session", expected_version=task.version)
            approved = built.runtime.approve(
                actor_id=LOCAL_OPERATOR_ACTOR, task_id=task_id,
                session_id=built.approval_session_id, expected_version=task.version,
            )
            self.assertEqual(approved.state, "queued")
            self.assertEqual(approved.version, task.version + 2)
    def test_approval_from_previous_process_cannot_be_claimed_after_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            first = build_local_worker_runtime(directory, FakeProvider())
            plan = first.coordinator.submit(
                actor_id=LOCAL_OPERATOR_ACTOR,
                idempotency_key="approval-session-restart",
                subtasks=[SubtaskSpec("research", "Collect public sources", ("research",))],
            )
            plan = first.coordinator.advance(actor_id=LOCAL_OPERATOR_ACTOR, plan_id=plan.plan_id)
            task_id = plan.subtasks[0].task_id
            task = first.runtime.get(actor_id=LOCAL_OPERATOR_ACTOR, task_id=task_id)
            first.runtime.approve(actor_id=LOCAL_OPERATOR_ACTOR, task_id=task_id,
                                  session_id=first.approval_session_id, expected_version=task.version)
            second = build_local_worker_runtime(directory, FakeProvider())
            self.assertIsNone(second.supervisor.worker.run_once())
            queued = second.runtime.get(actor_id=LOCAL_OPERATOR_ACTOR, task_id=task_id)
            self.assertEqual(queued.state, "queued")
            reapproved = second.runtime.approve(
                actor_id=LOCAL_OPERATOR_ACTOR, task_id=task_id,
                session_id=second.approval_session_id, expected_version=queued.version,
            )
            self.assertIsNotNone(second.supervisor.worker.run_once())
            self.assertEqual(second.runtime.get(actor_id=LOCAL_OPERATOR_ACTOR, task_id=task_id).state, "succeeded")
            self.assertEqual(reapproved.version, queued.version + 2)
    def test_local_operator_can_cancel_a_plan_through_authorized_runtime(self):
        with tempfile.TemporaryDirectory() as directory:
            built = build_local_worker_runtime(directory, FakeProvider())
            plan = built.coordinator.submit(
                actor_id=LOCAL_OPERATOR_ACTOR,
                idempotency_key="local-plan-cancel",
                subtasks=[SubtaskSpec("research", "Collect public sources", ("research",))],
            )
            plan = built.coordinator.advance(
                actor_id=LOCAL_OPERATOR_ACTOR, plan_id=plan.plan_id,
            )
            task_id = plan.subtasks[0].task_id
            cancelled = built.coordinator.cancel_plan(
                actor_id=LOCAL_OPERATOR_ACTOR, plan_id=plan.plan_id,
            )
            self.assertEqual(cancelled.subtasks[0].status, "cancelled")
            self.assertEqual(
                built.runtime.get(actor_id=LOCAL_OPERATOR_ACTOR, task_id=task_id).state,
                "cancelled",
            )

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


    def test_natural_language_plan_routes_into_local_specialist_dag(self):
        with tempfile.TemporaryDirectory() as directory:
            built = build_local_worker_runtime(directory, FakeProvider())
            proposed = Planner(max_steps=10).decompose(
                "Research and build a secure app, then test and report results"
            )
            plan = built.coordinator.submit(
                actor_id=LOCAL_OPERATOR_ACTOR,
                idempotency_key="planner-integration",
                subtasks=[
                    SubtaskSpec(
                        subtask_id=item.task_id,
                        description=item.description,
                        required_capabilities=item.required_capabilities,
                        depends_on=item.depends_on,
                        requires_approval=item.requires_approval,
                    )
                    for item in proposed
                ],
            )
            plan = built.coordinator.advance(
                actor_id=LOCAL_OPERATOR_ACTOR, plan_id=plan.plan_id,
            )
            by_id = {item.subtask_id: item for item in plan.subtasks}
            self.assertEqual(by_id["research"].agent_id, "research")
            self.assertIsNotNone(by_id["research"].task_id)
            self.assertIsNone(by_id["implementation"].task_id)
            self.assertIsNone(by_id["verification"].task_id)
            self.assertIsNone(by_id["summary"].task_id)
            self.assertEqual(by_id["implementation"].depends_on, ("research",))

    def test_local_supervisor_uses_bounded_parallelism(self):
        with tempfile.TemporaryDirectory() as directory:
            built = build_local_worker_runtime(directory, FakeProvider(), max_workers=3)
            self.assertEqual(built.supervisor.max_workers, 3)
            with self.assertRaises(ValueError):
                build_local_worker_runtime(directory, FakeProvider(), max_workers=5)

    def test_cycle_limit_is_bounded(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                build_local_worker_runtime(
                    directory, FakeProvider(), max_tasks_per_cycle=11,
                )


    def test_session_worker_reuses_existing_store_and_requires_live_owner_session(self):
        from automation.agent_coordinator import SpecialistAgent, SpecialistAgentCoordinator
        from automation.task_runtime import TaskRuntime
        from automation.workflow_store import WorkflowRunStore

        with tempfile.TemporaryDirectory() as directory:
            session_state = {"live": True, "token": "owner-session-token"}
            store = WorkflowRunStore(str(Path(directory) / "tasks.sqlite3"))
            owner_actions = {
                "task:create", "task:list", "task:read", "task:approve",
                "task:cancel", "task:session:revoke",
                "agent:plan", "agent:dispatch", "agent:read", "agent:cancel",
            }
            runtime = TaskRuntime(
                store,
                authorize=lambda actor, action, _task: (
                    actor == "owner-1" and action in owner_actions
                ) or actor == WORKER_SERVICE_ACTOR,
                verify_session=lambda actor, token: (
                    actor == "owner-1" and session_state["live"]
                    and token == session_state["token"]
                ),
            )
            coordinator = SpecialistAgentCoordinator(
                runtime, str(Path(directory) / "tasks.sqlite3"),
                [SpecialistAgent("research", "Research", frozenset({"research"}), "emit")],
                authorize=lambda actor, action, _plan: (
                    actor == "owner-1" and action in {"agent:plan", "agent:dispatch", "agent:read"}
                ),
            )
            plan = coordinator.submit(
                actor_id="owner-1", idempotency_key="same-store",
                subtasks=[SubtaskSpec("research", "Research task", ("research",))],
            )
            plan = coordinator.advance(actor_id="owner-1", plan_id=plan.plan_id)
            task_id = plan.subtasks[0].task_id
            task = runtime.get(actor_id="owner-1", task_id=task_id)
            runtime.approve(
                actor_id="owner-1", task_id=task_id,
                session_id="owner-session-token", expected_version=task.version,
            )

            built = build_session_worker_runtime(
                runtime, FakeProvider(), owner_actor_id="owner-1",
                approval_session_id="owner-session-token",
            )
            self.assertIs(built.runtime, runtime)
            self.assertIs(built.supervisor.runtime, runtime)
            self.assertIs(built.runtime.store, store)
            self.assertEqual(built.service.status.state, "stopped")
            self.assertTrue(runtime._allowed(WORKER_SERVICE_ACTOR, "task:complete", task_id))
            self.assertFalse(runtime._allowed(WORKER_SERVICE_ACTOR, "task:list", task_id))

            built.service.start()
            report = built.service.run_cycle()
            self.assertEqual(report.succeeded_count, 1)
            self.assertEqual(runtime.get(actor_id="owner-1", task_id=task_id).state, "succeeded")
            self.assertTrue(built.service.stop())

            session_state["live"] = False
            self.assertFalse(runtime._allowed(WORKER_SERVICE_ACTOR, "task:complete", task_id))
            self.assertFalse(runtime._allowed(WORKER_SERVICE_ACTOR, "task:recover", task_id))
            with self.assertRaises(PermissionError):
                runtime.list_task_summaries(actor_id=WORKER_SERVICE_ACTOR)

    def test_session_worker_rejects_forged_or_missing_session_verifier(self):
        from automation.task_runtime import TaskRuntime
        from automation.workflow_store import WorkflowRunStore

        with tempfile.TemporaryDirectory() as directory:
            store = WorkflowRunStore(str(Path(directory) / "tasks.sqlite3"))
            runtime = TaskRuntime(
                store, authorize=lambda *_: True,
                verify_session=lambda actor, token: (
                    actor == "owner-1" and token == "real-session"
                ),
            )
            with self.assertRaises(ValueError):
                build_session_worker_runtime(
                    runtime, FakeProvider(), owner_actor_id="owner-1",
                    approval_session_id="forged-session",
                )
            self.assertIsNone(runtime.internal_authorize)


if __name__ == "__main__":
    unittest.main()
