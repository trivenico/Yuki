import tempfile
import unittest
from threading import Barrier, Event, Lock, Thread
from pathlib import Path

from automation.agent_coordinator import (
    SpecialistAgent, SpecialistAgentCoordinator, SubtaskSpec,
)
from automation.specialist_worker import (
    SpecialistTaskWorker, build_provider_specialist_handlers, build_handlers_from_provider_manager,
)
from automation.task_runtime import TaskRuntime
from automation.workflow_store import WorkflowRunStore


class FakeProvider:
    def __init__(self, output="draft output", available=True):
        self.output = output
        self.available = available
        self.prompts = []
        self.close_calls = 0

    def close(self):
        self.close_calls += 1

    def is_available(self):
        return self.available

    def generate(self, prompt):
        self.prompts.append(prompt)
        return self.output


class SpecialistWorkerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        db = str(Path(self.tmp.name) / "yuki.sqlite3")
        self.store = WorkflowRunStore(db)
        grants = {"task:create", "task:read", "task:progress", "task:artifact",
                  "task:complete", "task:fail", "task:cancel"}
        self.runtime = TaskRuntime(
            self.store,
            authorize=lambda actor, action, _task: actor in {"alice", "worker-service"} and action in grants,
        )
        self.coordinator = SpecialistAgentCoordinator(
            self.runtime, db,
            [SpecialistAgent("code-review", "Code review", frozenset({"coding"}), "emit")],
            authorize=lambda actor, action, _plan: actor == "alice" and action in {
                "agent:plan", "agent:dispatch", "agent:read"
            },
        )

    def create_task(self, *, approval=False):
        plan = self.coordinator.submit(
            actor_id="alice", idempotency_key="worker-task-" + str(approval),
            subtasks=[SubtaskSpec("implement", "Propose a safe patch", ("coding",),
                                  requires_approval=approval)],
        )
        plan = self.coordinator.advance(actor_id="alice", plan_id=plan.plan_id)
        return plan.subtasks[0].task_id

    def worker(self, handlers):
        return SpecialistTaskWorker(
            self.runtime, worker_id="worker-a", service_actor_id="worker-service",
            handlers=handlers,
        )

    def test_registered_handler_executes_and_persists_bounded_result(self):
        task_id = self.create_task()
        result = self.worker({"code-review": lambda text: "reviewed: " + text}).run_once()
        self.assertEqual(result.task_id, task_id)
        self.assertEqual(result.state, "succeeded")
        self.assertEqual(result.result["specialist"], "code-review")
        self.assertEqual(result.result["output"], "reviewed: Propose a safe patch")
        self.assertEqual(result.result["execution"], "text-only")
        self.assertEqual(self.runtime.events(actor_id="worker-service", task_id=task_id)[-1].kind,
                         "task.completed")

    def test_unapproved_task_is_not_claimed(self):
        task_id = self.create_task(approval=True)
        self.assertIsNone(self.worker({"code-review": lambda _: "should not run"}).run_once())
        self.assertEqual(self.store.get(task_id).state, "queued")

    def test_approved_task_can_execute(self):
        task_id = self.create_task(approval=True)
        queued = self.store.get(task_id)
        self.store.approve(task_id, actor_id="reviewer", session_id="secure-session",
                           expected_version=queued.version)
        result = self.worker({"code-review": lambda _: "approved result"}).run_once()
        self.assertEqual(result.task_id, task_id)
        self.assertEqual(result.state, "succeeded")

    def test_session_revocation_after_claim_blocks_provider_invocation(self):
        task_id = self.create_task(approval=True)
        queued = self.store.get(task_id)
        self.store.approve(
            task_id, actor_id="reviewer", session_id="revoked-before-dispatch",
            expected_version=queued.version,
        )
        original_claim = self.store.claim_next

        def claim_then_revoke(*, worker_id, lease_seconds=60):
            claimed = original_claim(worker_id=worker_id, lease_seconds=lease_seconds)
            if claimed is not None:
                # Exercise the same public path used by desktop logout:
                # mark the session revoked before auth credentials are retired.
                self.runtime.revoke_session_approvals("revoked-before-dispatch")
            return claimed

        self.store.claim_next = claim_then_revoke
        calls = []
        result = self.worker({
            "code-review": lambda argument: calls.append(argument) or "must not be sent",
        }).run_once()
        self.assertEqual(calls, [])
        self.assertEqual(result.task_id, task_id)
        self.assertEqual(result.state, "failed")
        self.assertEqual(result.error_code, "specialist_handler_failed")

    def test_cancellation_during_handler_prevents_late_completion(self):
        task_id = self.create_task()
        started = Event()
        release = Event()
        outcome = {}

        def slow_handler(_argument):
            started.set()
            if not release.wait(timeout=5):
                raise TimeoutError("test handler timed out")
            return "late provider response"

        worker = self.worker({"code-review": slow_handler})
        thread = Thread(target=lambda: outcome.setdefault("result", worker.run_once()))
        thread.start()
        self.assertTrue(started.wait(timeout=5), "worker did not start handler")
        running = self.store.get(task_id)
        cancelled = self.runtime.cancel(
            actor_id="alice", task_id=task_id, expected_version=running.version,
        )
        self.assertEqual(cancelled.state, "cancelled")
        release.set()
        thread.join(timeout=5)
        self.assertFalse(thread.is_alive(), "worker did not exit after handler returned")
        self.assertEqual(outcome["result"].state, "cancelled")
        self.assertEqual(self.store.get(task_id).state, "cancelled")
        events = self.runtime.events(actor_id="worker-service", task_id=task_id)
        self.assertEqual(events[-1].kind, "task.cancelled")

    def test_handler_failure_is_redacted_and_terminal(self):
        task_id = self.create_task()
        result = self.worker({
            "code-review": lambda _: (_ for _ in ()).throw(RuntimeError("secret prompt and token")),
        }).run_once()
        self.assertEqual(result.task_id, task_id)
        self.assertEqual(result.state, "failed")
        self.assertEqual(result.error_code, "specialist_handler_failed")
        self.assertNotIn("secret", str(result.result))
        self.assertEqual(self.runtime.events(actor_id="worker-service", task_id=task_id)[-1].kind,
                         "task.failed")

    def test_provider_handlers_are_bounded_and_role_specific(self):
        provider = FakeProvider(output="x" * 20_000)
        handlers = build_provider_specialist_handlers(provider)
        self.assertEqual(set(handlers), {"research", "coding", "testing", "reporting"})
        result = handlers["coding"]("Review this change")
        self.assertEqual(len(result), 12_000)
        self.assertIn("Do not claim files were changed", provider.prompts[0])
        with self.assertRaises(ValueError):
            handlers["research"]("x" * 2_001)

    def test_hybrid_specialists_use_only_explicit_cloud_task_allowlist(self):
        from providers.hybrid import HybridProvider
        local = FakeProvider(output="local result")
        cloud = FakeProvider(output="cloud result")
        hybrid = HybridProvider(local, cloud, cloud_tasks=("research",))
        handlers = build_provider_specialist_handlers(hybrid)

        self.assertEqual(handlers["research"]("Research this"), "cloud result")
        self.assertEqual(handlers["coding"]("Review this"), "local result")
        self.assertEqual(len(cloud.prompts), 1)
        self.assertEqual(len(local.prompts), 1)

    def test_provider_unavailable_fails_closed(self):
        handlers = build_provider_specialist_handlers(FakeProvider(available=False))
        with self.assertRaises(RuntimeError):
            handlers["reporting"]("Summarize the supplied facts")

    def test_selected_provider_manager_is_wired_to_registered_agent_roles(self):
        class Manager:
            def lease_adapter(self, adapter):
                from contextlib import nullcontext
                return nullcontext(adapter)

            def create_selected_adapter(self):
                return FakeProvider(output="provider result")

        handlers = build_handlers_from_provider_manager(
            Manager(), {"code-review": "coding", "qa-check": "testing"}
        )
        self.assertEqual(set(handlers), {"code-review", "qa-check"})
        self.assertEqual(handlers["code-review"]("Review this patch"), "provider result")


    def test_specialist_handler_holds_manager_lease_for_provider_operation(self):
        from contextlib import contextmanager

        class LeaseManager:
            def __init__(self):
                self.adapter = FakeProvider(output="leased result")
                self.active = 0
            def create_selected_adapter(self):
                return self.adapter
            @contextmanager
            def lease_adapter(self, adapter):
                self.assert_adapter(adapter)
                self.active += 1
                try:
                    yield adapter
                finally:
                    self.active -= 1
            def assert_adapter(self, adapter):
                if adapter is not self.adapter:
                    raise ValueError("foreign adapter")

        manager = LeaseManager()
        original_generate = manager.adapter.generate
        def generate(prompt):
            if manager.active != 1:
                raise AssertionError("provider call ran without its manager lease")
            return original_generate(prompt)
        manager.adapter.generate = generate
        handlers = build_handlers_from_provider_manager(
            manager, {"research-agent": "research"}
        )
        self.assertEqual(handlers["research-agent"]("Summarize this"), "leased result")
        self.assertEqual(manager.active, 0)

    def test_provider_preferences_route_each_specialist_independently(self):
        class RoutedManager:
            def __init__(self):
                self.calls = []
                self.selected = FakeProvider(output="selected")
                self.cloud = FakeProvider(output="cloud-routed")
                self.released = []
            def create_selected_adapter(self):
                return self.selected
            def create_routed_adapter(self, *, required, preference, allow_cloud):
                self.calls.append((required, preference, allow_cloud))
                return self.cloud
            def release_routed_adapter(self, adapter):
                self.released.append(adapter)

        manager = RoutedManager()
        handlers = build_handlers_from_provider_manager(
            manager,
            {"local-review": "coding", "cloud-research": "research"},
            provider_preferences={"cloud-research": ("openai", "gemini")},
            allow_cloud=True,
        )
        self.assertEqual(handlers["local-review"]("Review this"), "selected")
        self.assertEqual(handlers["cloud-research"]("Research this"), "cloud-routed")
        self.assertEqual(manager.calls, [(("text",), ("openai", "gemini"), True)])
        self.assertEqual(manager.released, [])

    def test_routed_manager_without_release_hook_fails_before_allocation(self):
        class RoutedManager:
            def __init__(self):
                self.create_calls = 0
            def create_selected_adapter(self):
                raise AssertionError("selected adapter must not be used")
            def create_routed_adapter(self, **kwargs):
                self.create_calls += 1
                return FakeProvider()

        manager = RoutedManager()
        with self.assertRaisesRegex(TypeError, "release_routed_adapter"):
            build_handlers_from_provider_manager(
                manager, {"research-agent": "research"},
                provider_preferences={"research-agent": ("openai",)},
                allow_cloud=True,
            )
        self.assertEqual(manager.create_calls, 0)

    def test_provider_preference_failures_never_fall_back_to_selected_provider(self):
        class RoutedManager:
            def __init__(self):
                self.selected = FakeProvider(output="must-not-be-used")
            def create_selected_adapter(self):
                return self.selected
            def create_routed_adapter(self, **kwargs):
                raise RuntimeError("no eligible route")
            def release_routed_adapter(self, adapter):
                raise AssertionError("no adapter was returned to release")

        manager = RoutedManager()
        with self.assertRaisesRegex(RuntimeError, "no eligible route"):
            build_handlers_from_provider_manager(
                manager, {"research-agent": "research"},
                provider_preferences={"research-agent": ("openai",)},
                allow_cloud=True,
            )
        with self.assertRaises(ValueError):
            build_handlers_from_provider_manager(
                manager, {"research-agent": "research"},
                provider_preferences={"research-agent": ["openai"]},
            )

    def test_failed_routed_handler_build_releases_only_manager_owned_adapters(self):
        class RoutedManager:
            def __init__(self):
                self.created = FakeProvider()
                self.released = []
                self.calls = 0

            def create_selected_adapter(self):
                raise AssertionError("selected adapter must not be used")

            def create_routed_adapter(self, *, required, preference, allow_cloud):
                self.calls += 1
                if preference == ("openai",):
                    return self.created
                raise RuntimeError("second route unavailable")

            def release_routed_adapter(self, adapter):
                self.released.append(adapter)

        manager = RoutedManager()
        with self.assertRaisesRegex(RuntimeError, "second route unavailable"):
            build_handlers_from_provider_manager(
                manager,
                {"research-agent": "research", "coding-agent": "coding"},
                provider_preferences={
                    "research-agent": ("openai",),
                    "coding-agent": ("ollama",),
                },
                allow_cloud=True,
            )
        self.assertEqual(manager.calls, 2)
        self.assertEqual(manager.released, [manager.created])
        self.assertEqual(manager.created.close_calls, 0)

    def test_invalid_routed_adapter_is_returned_to_manager_for_release(self):
        class InvalidAdapter:
            pass

        class RoutedManager:
            def __init__(self):
                self.adapter = InvalidAdapter()
                self.released = []

            def create_selected_adapter(self):
                raise AssertionError("selected adapter must not be used")

            def create_routed_adapter(self, **kwargs):
                return self.adapter

            def release_routed_adapter(self, adapter):
                self.released.append(adapter)

        manager = RoutedManager()
        with self.assertRaises(TypeError):
            build_handlers_from_provider_manager(
                manager,
                {"research-agent": "research"},
                provider_preferences={"research-agent": ("ollama",)},
            )
        self.assertEqual(manager.released, [manager.adapter])

    def test_provider_manager_role_map_fails_closed(self):
        class MissingManager:
            pass

        with self.assertRaises(TypeError):
            build_handlers_from_provider_manager(MissingManager(), {"agent": "coding"})
        class EmptyManager:
            def lease_adapter(self, adapter):
                from contextlib import nullcontext
                return nullcontext(adapter)
            def create_selected_adapter(self):
                return None
        with self.assertRaises(RuntimeError):
            build_handlers_from_provider_manager(EmptyManager(), {"agent": "coding"})
        with self.assertRaises(ValueError):
            build_handlers_from_provider_manager(object(), {"agent": "shell"})

    def test_parallel_worker_pool_executes_independent_tasks_concurrently(self):
        plan = self.coordinator.submit(
            actor_id="alice", idempotency_key="parallel-plan",
            subtasks=[
                SubtaskSpec("first", "First independent review", ("coding",), requires_approval=False),
                SubtaskSpec("second", "Second independent review", ("coding",), requires_approval=False),
            ],
        )
        plan = self.coordinator.advance(actor_id="alice", plan_id=plan.plan_id)
        self.assertEqual(len([s for s in plan.subtasks if s.task_id]), 2)

        rendezvous = Barrier(2, timeout=5)
        active_lock = Lock()
        active = 0
        peak_active = 0

        def handler(argument):
            nonlocal active, peak_active
            with active_lock:
                active += 1
                peak_active = max(peak_active, active)
            try:
                rendezvous.wait()
                return "completed: " + argument
            finally:
                with active_lock:
                    active -= 1

        results = self.worker({"code-review": handler}).run_concurrently(max_workers=2, max_tasks=2)
        self.assertEqual(len(results), 2)
        self.assertTrue(all(result.state == "succeeded" for result in results))
        self.assertEqual(peak_active, 2)

    def test_parallel_worker_pool_respects_task_budget_and_argument_bounds(self):
        plan = self.coordinator.submit(
            actor_id="alice", idempotency_key="parallel-budget-plan",
            subtasks=[
                SubtaskSpec("one", "First task", ("coding",), requires_approval=False),
                SubtaskSpec("two", "Second task", ("coding",), requires_approval=False),
                SubtaskSpec("three", "Third task", ("coding",), requires_approval=False),
            ],
        )
        plan = self.coordinator.advance(actor_id="alice", plan_id=plan.plan_id)
        results = self.worker({"code-review": lambda text: "ok " + text}).run_concurrently(
            max_workers=2, max_tasks=2,
        )
        self.assertEqual(len(results), 2)
        self.assertEqual(sum(s.state == "queued" for s in self.store.list_runs()), 1)
        with self.assertRaises(ValueError):
            self.worker({"code-review": lambda _: "ok"}).run_concurrently(max_workers=17)
        with self.assertRaises(ValueError):
            self.worker({"code-review": lambda _: "ok"}).run_concurrently(max_tasks=101)

    def test_invalid_handler_registry_rejected(self):
        with self.assertRaises(ValueError):
            self.worker({})
        with self.assertRaises(ValueError):
            self.worker({"not valid": lambda _: "no"})


if __name__ == "__main__":
    unittest.main()
