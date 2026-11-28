import os
import tempfile
import unittest
from pathlib import Path

from automation.agent_coordinator import SubtaskSpec
from core.planner import Planner

from automation.local_worker_runtime import (
    LOCAL_OPERATOR_ACTOR,
    WORKER_SERVICE_ACTOR,
    build_local_worker_runtime,
)


class FakeProvider:
    def __init__(self):
        self.close_calls = 0

    def close(self):
        self.close_calls += 1

    def is_available(self):
        return True

    def generate(self, prompt):
        return "bounded specialist output"


@unittest.skipUnless(os.name == "posix", "local worker private-profile composition is POSIX-only until Windows ACL verification exists")
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
            self.assertIsNotNone(built.runtime.store.maintenance_fence)
            self.assertEqual(built.runtime.store.maintenance_fence.profile_root, Path(directory))
            self.assertIs(
                built.coordinator.maintenance_fence,
                built.runtime.store.maintenance_fence,
            )

    def test_provider_setup_failure_preserves_initialized_profile_for_retry(self):
        from unittest.mock import patch
        from automation.workflow_store import WorkflowRunStore
        from core.maintenance_fence import MaintenanceFence

        with tempfile.TemporaryDirectory() as directory:
            profile = Path(directory) / "yuki-profile"
            profile.mkdir(mode=0o700)
            with patch(
                "automation.local_worker_runtime.build_provider_specialist_handlers",
                side_effect=RuntimeError("provider setup failed"),
            ):
                with self.assertRaisesRegex(RuntimeError, "provider setup failed"):
                    build_local_worker_runtime(profile, FakeProvider())

            database = profile / "yuki_tasks.sqlite3"
            self.assertTrue(database.is_file())
            fence = MaintenanceFence(profile)
            store = WorkflowRunStore(str(database), maintenance_fence=fence)
            preserved = store.create(
                idempotency_key="owner-data-before-retry",
                workflow_name="preserved-task",
                definition=[{"name": "step", "action": "emit", "argument": "keep"}],
            )

            retried = build_local_worker_runtime(profile, FakeProvider())
            recovered = retried.runtime.store.get(preserved.run_id)
            self.assertEqual(recovered.workflow_name, "preserved-task")
            self.assertEqual(recovered.definition[0]["argument"], "keep")
            self.assertIs(retried.runtime.store.maintenance_fence, retried.coordinator.maintenance_fence)

    def test_factory_rejects_permissive_workspace_instead_of_repairing_it(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory) / "shared-workspace"
            workspace.mkdir(mode=0o755)
            if __import__("os").name == "posix":
                workspace.chmod(0o755)
                from core.private_profile import PrivateProfileError
                with self.assertRaises(PrivateProfileError):
                    build_local_worker_runtime(workspace, FakeProvider())
                self.assertEqual(workspace.stat().st_mode & 0o777, 0o755)
            else:
                self.skipTest("private profile ACL validation is not implemented on Windows")

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

    def test_runtime_shutdown_does_not_close_borrowed_provider(self):
        with tempfile.TemporaryDirectory() as directory:
            provider = FakeProvider()
            built = build_local_worker_runtime(directory, provider)
            self.assertTrue(built.shutdown(timeout_seconds=1))
            self.assertEqual(provider.close_calls, 0)

    def test_local_runtime_shutdown_revokes_ephemeral_session_before_drain(self):
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as directory:
            built = build_local_worker_runtime(directory, FakeProvider())
            with patch.object(built.runtime, "revoke_session_approvals", wraps=built.runtime.revoke_session_approvals) as revoke, patch.object(
                built.supervisor, "shutdown", return_value=True,
            ) as drain:
                self.assertTrue(built.shutdown(timeout_seconds=5))
            revoke.assert_called_once_with(built.approval_session_id)
            drain.assert_called_once_with(timeout_seconds=5)

    def test_local_runtime_shutdown_keeps_revocation_when_drain_times_out(self):
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as directory:
            built = build_local_worker_runtime(directory, FakeProvider())
            with patch.object(
                built.supervisor, "shutdown", return_value=False,
            ) as drain:
                self.assertFalse(built.shutdown(timeout_seconds=0))
            drain.assert_called_once_with(timeout_seconds=0)
            session_hash = __import__("hashlib").sha256(
                built.approval_session_id.encode("utf-8")
            ).hexdigest()
            with built.runtime.store._connect() as db:
                marker = db.execute(
                    "SELECT 1 FROM revoked_approval_sessions WHERE session_hash=?",
                    (session_hash,),
                ).fetchone()
            self.assertIsNotNone(marker)

    def test_local_runtime_shutdown_validates_timeout_before_revocation(self):
        with tempfile.TemporaryDirectory() as directory:
            built = build_local_worker_runtime(directory, FakeProvider())
            with self.assertRaises(ValueError):
                built.shutdown(timeout_seconds=-1)
            session_hash = __import__("hashlib").sha256(
                built.approval_session_id.encode("utf-8")
            ).hexdigest()
            with built.runtime.store._connect() as db:
                marker = db.execute(
                    "SELECT 1 FROM revoked_approval_sessions WHERE session_hash=?",
                    (session_hash,),
                ).fetchone()
            self.assertIsNone(marker)

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

    def test_local_supervisor_defaults_to_one_worker_without_memory_estimate(self):
        with tempfile.TemporaryDirectory() as directory:
            built = build_local_worker_runtime(directory, FakeProvider(), max_workers=3)
            self.assertEqual(built.supervisor.max_workers, 1)
            self.assertIn("per-worker memory estimate not configured", built.worker_budget.reason)
            self.assertEqual(built.resource_status.reason, "not_configured")
            self.assertFalse(built.resource_status.paused)

    def test_local_supervisor_parallelism_uses_resource_budget(self):
        from types import SimpleNamespace
        from unittest.mock import patch

        resources = SimpleNamespace(
            logical_cpu_count=4,
            effective_cpu_count=4,
            memory_effective_available_bytes=4_000_000_000,
        )
        with tempfile.TemporaryDirectory() as directory:
            with patch("automation.local_worker_runtime.ResourceInventory.detect", return_value=resources):
                built = build_local_worker_runtime(
                    directory, FakeProvider(), max_workers=3,
                    per_worker_memory_bytes=500_000_000,
                    memory_reserve_bytes=500_000_000,
                )
            self.assertEqual(built.supervisor.max_workers, 3)
            self.assertEqual(built.worker_budget.allowed_concurrency, 3)
            self.assertEqual(built.worker_budget.memory_budget_bytes, 3_500_000_000)

    def test_local_supervisor_parallelism_is_limited_by_available_memory(self):
        from types import SimpleNamespace
        from unittest.mock import patch

        resources = SimpleNamespace(
            logical_cpu_count=8,
            effective_cpu_count=8,
            memory_effective_available_bytes=1_500_000_000,
        )
        with tempfile.TemporaryDirectory() as directory:
            with patch("automation.local_worker_runtime.ResourceInventory.detect", return_value=resources):
                built = build_local_worker_runtime(
                    directory, FakeProvider(), max_workers=4,
                    per_worker_memory_bytes=1_000_000_000,
                )
            self.assertEqual(built.supervisor.max_workers, 1)
            self.assertEqual(built.worker_budget.allowed_concurrency, 1)

    def test_local_supervisor_rejects_invalid_memory_budget(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                build_local_worker_runtime(
                    directory, FakeProvider(), per_worker_memory_bytes=0,
                )
            with self.assertRaises(ValueError):
                build_local_worker_runtime(
                    directory, FakeProvider(), memory_reserve_bytes=-1,
                )
            with self.assertRaises(ValueError):
                build_local_worker_runtime(directory, FakeProvider(), max_workers=5)

    def test_memory_pressure_hysteresis_requires_resume_threshold(self):
        from types import SimpleNamespace
        from unittest.mock import patch

        readings = [
            SimpleNamespace(logical_cpu_count=2, memory_effective_available_bytes=value)
            for value in (2_000, 400, 600, 800, 400)
        ]
        with tempfile.TemporaryDirectory() as directory:
            with patch(
                "automation.local_worker_runtime.ResourceInventory.detect",
                side_effect=readings,
            ):
                built = build_local_worker_runtime(
                    directory, FakeProvider(),
                    memory_reserve_bytes=500,
                    memory_resume_bytes=750,
                )
                reports = [built.supervisor.run_cycle() for _ in range(4)]
        self.assertEqual(
            [report.resource_paused for report in reports],
            [True, True, False, True],
        )
        self.assertTrue(built.resource_status.paused)
        self.assertEqual(built.resource_status.available_bytes, 400)
        self.assertEqual(built.resource_status.pause_threshold_bytes, 500)
        self.assertEqual(built.resource_status.resume_threshold_bytes, 750)
        self.assertEqual(built.resource_status.reason, "below_resume_threshold")

    def test_memory_pressure_pauses_before_lease_recovery_or_task_claim(self):
        from types import SimpleNamespace
        from unittest.mock import patch

        readings = [
            SimpleNamespace(
                logical_cpu_count=2, effective_cpu_count=2,
                memory_effective_available_bytes=4_000,
            ),
            SimpleNamespace(
                logical_cpu_count=2, effective_cpu_count=2,
                memory_effective_available_bytes=400,
            ),
        ]
        with tempfile.TemporaryDirectory() as directory:
            with patch(
                "automation.local_worker_runtime.ResourceInventory.detect",
                side_effect=readings,
            ):
                built = build_local_worker_runtime(
                    directory, FakeProvider(), memory_reserve_bytes=500,
                )
                with patch.object(built.runtime, "recover_expired") as recover, patch.object(
                    built.supervisor.worker, "run_once",
                ) as run_once:
                    report = built.supervisor.run_cycle()
                recover.assert_not_called()
                run_once.assert_not_called()
        self.assertTrue(report.resource_paused)
        self.assertEqual(report.processed_count, 0)
        self.assertEqual(built.resource_status.reason, "below_pause_threshold")

    def test_unknown_memory_and_probe_errors_fail_closed(self):
        from types import SimpleNamespace
        from unittest.mock import patch

        healthy = SimpleNamespace(
            logical_cpu_count=2, effective_cpu_count=2,
            memory_effective_available_bytes=4_000,
        )
        with tempfile.TemporaryDirectory() as directory:
            with patch(
                "automation.local_worker_runtime.ResourceInventory.detect",
                side_effect=[healthy, RuntimeError("resource probe failed")],
            ):
                built = build_local_worker_runtime(
                    directory, FakeProvider(), memory_reserve_bytes=500,
                )
                report = built.supervisor.run_cycle()
        self.assertTrue(report.resource_paused)
        self.assertEqual(built.resource_status.available_bytes, None)
        self.assertTrue(built.resource_status.paused)
        self.assertEqual(built.resource_status.reason, "memory_unavailable")

    def test_unknown_memory_stays_paused_until_resume_threshold_is_observed(self):
        from types import SimpleNamespace
        from unittest.mock import patch

        readings = [
            SimpleNamespace(
                logical_cpu_count=2, effective_cpu_count=2,
                memory_effective_available_bytes=4_000,
            ),
            SimpleNamespace(
                logical_cpu_count=2, effective_cpu_count=2,
                memory_effective_available_bytes=None,
            ),
            SimpleNamespace(
                logical_cpu_count=2, effective_cpu_count=2,
                memory_effective_available_bytes=700,
            ),
            SimpleNamespace(
                logical_cpu_count=2, effective_cpu_count=2,
                memory_effective_available_bytes=800,
            ),
        ]
        with tempfile.TemporaryDirectory() as directory:
            with patch(
                "automation.local_worker_runtime.ResourceInventory.detect",
                side_effect=readings,
            ):
                built = build_local_worker_runtime(
                    directory, FakeProvider(), memory_reserve_bytes=500,
                    memory_resume_bytes=750,
                )
                reports = [built.supervisor.run_cycle() for _ in range(3)]
        self.assertEqual(
            [report.resource_paused for report in reports],
            [True, True, False],
        )
        self.assertEqual(built.resource_status.reason, "recovered")
        self.assertFalse(built.resource_status.paused)

    def test_memory_resume_threshold_must_not_be_below_pause_threshold(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                build_local_worker_runtime(
                    directory, FakeProvider(),
                    memory_reserve_bytes=500,
                    memory_resume_bytes=499,
                )
            with self.assertRaises(ValueError):
                build_local_worker_runtime(
                    directory, FakeProvider(),
                    memory_resume_bytes=500,
                )

    def test_cycle_limit_is_bounded(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                build_local_worker_runtime(
                    directory, FakeProvider(), max_tasks_per_cycle=11,
                )


@unittest.skipUnless(os.name == "nt", "Windows-specific fail-closed boundary")
class WindowsLocalWorkerBoundaryTests(unittest.TestCase):
    def test_private_profile_validation_fails_closed_without_creating_directory(self):
        from core.private_profile import PrivateProfileError, ensure_private_profile_directory

        with tempfile.TemporaryDirectory() as directory:
            profile = Path(directory) / "private-profile"
            with self.assertRaises(PrivateProfileError):
                ensure_private_profile_directory(profile)
            self.assertFalse(profile.exists())

    def test_local_worker_factory_fails_closed_without_creating_task_store(self):
        from core.private_profile import PrivateProfileError

        with tempfile.TemporaryDirectory() as directory:
            profile = Path(directory) / "private-profile"
            with self.assertRaises(PrivateProfileError):
                build_local_worker_runtime(profile, FakeProvider())
            self.assertFalse(profile.exists())
            self.assertFalse((profile / "yuki_tasks.sqlite3").exists())


if __name__ == "__main__":
    unittest.main()
