import tempfile
import time
import unittest
from threading import Barrier, Event, Lock, Thread
from pathlib import Path

from automation.agent_coordinator import (
    SpecialistAgent, SpecialistAgentCoordinator, SubtaskSpec,
)
from automation.agent_execution_spec import AgentExecutionSpec
from automation.specialist_worker import (
    SpecialistTaskWorker, build_provider_specialist_handlers, build_handlers_from_provider_manager,
)
from automation.worker_registry import WorkerRegistry
from automation.registered_specialist_worker import RegisteredSpecialistWorker
from automation.task_runtime import TaskRuntime
from automation.workflow_store import WorkflowConflict, WorkflowRunStore
from core.task_artifacts import TaskArtifactStore
from core.task_workspace import TaskWorkspaceResolver, WorkspaceSecurityError


class FakeProvider:
    def __init__(self, output="draft output", available=True):
        self.output = output
        self.available = available
        self.prompts = []

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
        self.registry = WorkerRegistry(db)
        grants = {"task:create", "task:read", "task:progress", "task:artifact",
                  "task:complete", "task:fail", "task:cancel", "task:lease:renew",
                  "task:spec:register", "task:spec:read", "task:spec:list", "task:sandbox:execute", "task:sandbox:audit", "task:owner:override"}
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

    def test_worker_routes_sandbox_capability_through_authorized_profile(self):
        from types import SimpleNamespace
        from automation.agent_execution_spec import ExecutionBudget
        from core.sandbox_executor import SandboxExecutionProfile
        from core.sandbox_policy import SandboxPolicy

        spec = AgentExecutionSpec(
            spec_id="isolated-code-review", role="code-review",
            instructions="Run the approved isolated analysis tool.",
            capabilities=("sandbox.execute",), approval_policy="not_requested",
            budget=ExecutionBudget(max_runtime_seconds=5, max_memory_mb=128),
        )
        self.runtime.register_agent_spec(actor_id="alice", spec=spec)
        task = self.runtime.enqueue(
            actor_id="alice", idempotency_key="isolated-code-review-task",
            name="code-review", steps=[{
                "name": "run", "action": "emit", "argument": "ignored",
                "requires_approval": False,
            }], agent_execution_spec_id=spec.spec_id,
        )
        profile = SandboxExecutionProfile(
            spec_id=spec.spec_id,
            image="python@sha256:" + "a" * 64,
            policy=SandboxPolicy(timeout_seconds=5, memory_mb=128),
            command=("/usr/local/bin/yuki-approved-analysis", "--safe-mode"),
            workspace_resolver=lambda claimed: str(Path(self.tmp.name) / claimed.task_id),
        )

        class FakeSandbox:
            def __init__(self):
                self.calls = []

            def execute_authorized_task(self, **kwargs):
                self.calls.append(kwargs)
                outcome = SimpleNamespace(
                    stdout=b"isolated result", stderr=b"", exit_code=0,
                    duration_seconds=0.01, timed_out=False,
                    output_truncated=False, cancelled=False,
                )
                callback = kwargs.get("lifecycle_callback")
                if callback is not None:
                    callback("task.sandbox.started", "yuki-" + "c" * 32, None)
                    callback("task.sandbox.completed", "yuki-" + "c" * 32, outcome)
                return outcome

        sandbox = FakeSandbox()
        worker = SpecialistTaskWorker(
            self.runtime, worker_id="worker-a", service_actor_id="worker-service",
            handlers={"code-review": lambda _value: self.fail("ordinary handler must not run")},
            handler_capabilities={"code-review": ("sandbox.execute",)},
            sandbox_executor=sandbox, sandbox_profiles={spec.spec_id: profile},
        )
        result = worker.run_once()
        self.assertEqual(result.task_id, task.task_id)
        self.assertEqual(result.state, "succeeded")
        self.assertEqual(result.result["output"], "isolated result")
        self.assertEqual(len(sandbox.calls), 1)
        self.assertEqual(sandbox.calls[0]["task"].run_id, task.task_id)
        self.assertIs(sandbox.calls[0]["authorize"], self.runtime.is_authorized)
        lifecycle = self.runtime.events(actor_id="alice", task_id=task.task_id)
        self.assertEqual(
            [event.kind for event in lifecycle if event.kind.startswith("task.sandbox.")],
            ["task.sandbox.started", "task.sandbox.completed"],
        )
        self.assertEqual(lifecycle[-1].payload["image_digest"], "sha256:" + "a" * 64)

    def test_worker_rejects_sandbox_capability_without_operator_profile(self):
        from automation.agent_execution_spec import ExecutionBudget
        spec = AgentExecutionSpec(
            spec_id="missing-isolation-profile", role="code-review",
            instructions="Run isolated work only.", capabilities=("sandbox.execute",),
            approval_policy="not_requested",
            budget=ExecutionBudget(max_runtime_seconds=5, max_memory_mb=128),
        )
        self.runtime.register_agent_spec(actor_id="alice", spec=spec)
        task = self.runtime.enqueue(
            actor_id="alice", idempotency_key="missing-isolation-profile-task",
            name="code-review", steps=[{
                "name": "run", "action": "emit", "argument": "ignored",
                "requires_approval": False,
            }], agent_execution_spec_id=spec.spec_id,
        )
        worker = SpecialistTaskWorker(
            self.runtime, worker_id="worker-a", service_actor_id="worker-service",
            handlers={"code-review": lambda _value: "must not execute"},
            handler_capabilities={"code-review": ("sandbox.execute",)},
        )
        result = worker.run_once()
        self.assertEqual(result.task_id, task.task_id)
        self.assertEqual(result.state, "failed")

    def test_worker_rejects_spec_owned_by_a_different_task_account(self):
        spec = AgentExecutionSpec(
            spec_id="cross-account-worker-spec",
            role="code-review",
            instructions="Review supplied content safely.",
            approval_policy="not_requested",
            output_types=("text",),
        )
        self.runtime.register_agent_spec(actor_id="alice", spec=spec)
        task = self.runtime.enqueue(
            actor_id="alice", idempotency_key="cross-account-worker-task",
            name="code-review", steps=[{
                "name": "review", "action": "emit", "argument": "inspect",
                "requires_approval": False,
            }], agent_execution_spec_id=spec.spec_id,
        )
        # Simulate corrupted/mis-bound persisted task ownership. The worker must
        # resolve the spec against the task's owner, not globally by spec ID.
        with self.store._connect() as db:
            db.execute(
                "UPDATE workflow_runs SET owner_id=? WHERE run_id=?",
                ("bob", task.task_id),
            )
        called = []
        result = self.worker({
            "code-review": lambda value: called.append(value) or "must not execute",
        }).run_once()
        self.assertEqual(result.task_id, task.task_id)
        self.assertEqual(result.state, "failed")
        self.assertEqual(result.error_code, "agent_spec_integrity")
        self.assertEqual(called, [])

    def test_worker_dispatch_requires_registered_spec_digest_and_role(self):
        spec = AgentExecutionSpec(
            spec_id="code-review-dispatch",
            role="code-review",
            instructions="Review code safely.",
            approval_policy="not_requested",
            output_types=("text",),
        )
        self.runtime.register_agent_spec(actor_id="alice", spec=spec)
        task = self.runtime.enqueue(
            actor_id="alice", idempotency_key="spec-bound-worker-task",
            name="code-review", steps=[{
                "name": "review", "action": "emit", "argument": "inspect",
                "requires_approval": False,
            }], agent_execution_spec_id=spec.spec_id,
        )
        result = self.worker({"code-review": lambda text: "reviewed " + text}).run_once()
        self.assertEqual(result.task_id, task.task_id)
        self.assertEqual(result.state, "succeeded")
        self.assertEqual(result.agent_execution_spec_id, spec.spec_id)

    def test_worker_enforces_spec_step_budget_before_handler(self):
        from automation.agent_execution_spec import ExecutionBudget
        spec = AgentExecutionSpec(
            spec_id="step-budget-agent", role="code-review",
            instructions="Review safely.", approval_policy="not_requested",
            budget=ExecutionBudget(max_steps=1),
        )
        self.runtime.register_agent_spec(actor_id="alice", spec=spec)
        task = self.runtime.enqueue(
            actor_id="alice", idempotency_key="step-budget-task",
            name="code-review", steps=[
                {"name": "one", "action": "emit", "argument": "a", "requires_approval": False},
                {"name": "two", "action": "emit", "argument": "b", "requires_approval": False},
            ], agent_execution_spec_id=spec.spec_id,
        )
        called = []
        result = self.worker({"code-review": lambda value: called.append(value) or "ok"}).run_once()
        self.assertEqual(result.task_id, task.task_id)
        self.assertEqual(result.state, "failed")
        self.assertEqual(result.error_code, "agent_step_budget_exceeded")
        self.assertEqual(called, [])

    def test_replaced_worker_session_cannot_publish_handler_output(self):
        task_id = self.create_task()
        def replaced_handler(_argument):
            self.registry.register("worker-a")
            return "stale process output"
        worker = self.worker({"code-review": replaced_handler})
        registered = RegisteredSpecialistWorker(worker, self.registry)
        registered.start()
        try:
            with self.assertRaises(WorkflowConflict):
                registered.run_once()
        finally:
            registered.close()
        task = self.store.get(task_id)
        self.assertEqual(task.state, "running")
        self.assertIsNone(task.result)
        self.assertEqual(task.lease_owner, "worker-a")

    def test_worker_renews_task_lease_during_long_handler(self):
        self.create_task()
        worker = self.worker({"code-review": lambda _value: (time.sleep(0.25) or "finished")})
        worker._TASK_LEASE_HEARTBEAT_INTERVAL_SECONDS = 0.05
        renewals = []
        original_renew = self.runtime.renew_lease

        def tracked_renew(**kwargs):
            renewed = original_renew(**kwargs)
            renewals.append(renewed)
            return renewed

        self.runtime.renew_lease = tracked_renew
        result = worker.run_once()
        self.assertEqual(result.state, "succeeded")
        self.assertGreaterEqual(len(renewals), 1)
        self.assertEqual(renewals[-1].lease_owner, worker.worker_id)

    def test_task_heartbeat_stops_before_terminal_transition(self):
        self.create_task()
        worker = self.worker({"code-review": lambda _value: (time.sleep(0.15) or "finished")})
        worker._TASK_LEASE_HEARTBEAT_INTERVAL_SECONDS = 0.02
        renewals = []
        original_renew = self.runtime.renew_lease
        original_complete = self.runtime.complete

        def tracked_renew(**kwargs):
            result = original_renew(**kwargs)
            renewals.append(result)
            return result

        def delayed_complete(**kwargs):
            count_at_transition = len(renewals)
            time.sleep(0.08)
            self.assertEqual(len(renewals), count_at_transition)
            return original_complete(**kwargs)

        self.runtime.renew_lease = tracked_renew
        self.runtime.complete = delayed_complete
        result = worker.run_once()
        self.assertEqual(result.state, "succeeded")
        self.assertGreaterEqual(len(renewals), 1)

    def test_handler_failure_uses_version_after_lease_heartbeat_stops(self):
        self.create_task()

        def failing_handler(_value):
            time.sleep(0.15)
            raise RuntimeError("expected handler failure")

        worker = self.worker({"code-review": failing_handler})
        worker._TASK_LEASE_HEARTBEAT_INTERVAL_SECONDS = 0.02
        renewals = []
        original_renew = self.runtime.renew_lease

        def tracked_renew(**kwargs):
            result = original_renew(**kwargs)
            renewals.append(result)
            return result

        self.runtime.renew_lease = tracked_renew
        result = worker.run_once()
        self.assertEqual(result.state, "failed")
        self.assertGreaterEqual(len(renewals), 1)
        self.assertEqual(result.error_code, "specialist_handler_failed")

    def test_registered_worker_renews_task_lease_with_its_session(self):
        self.create_task()
        worker = self.worker({"code-review": lambda _value: (time.sleep(0.25) or "finished")})
        worker._TASK_LEASE_HEARTBEAT_INTERVAL_SECONDS = 0.05
        registered = RegisteredSpecialistWorker(
            worker, self.registry, heartbeat_interval=0.1,
        )
        try:
            result = registered.run_once()
            self.assertEqual(result.state, "succeeded")
            self.assertEqual(self.registry.list_workers()[0].status, "active")
        finally:
            registered.close()

    def test_worker_discards_handler_output_when_lease_renewal_fails(self):
        task_id = self.create_task()
        started = Event()
        release = Event()

        def slow_handler(_value):
            started.set()
            release.wait(2)
            return "must not be published"

        worker = self.worker({"code-review": slow_handler})
        worker._TASK_LEASE_HEARTBEAT_INTERVAL_SECONDS = 0.05

        def renewal_fails(**_kwargs):
            from automation.workflow_store import WorkflowConflict
            raise WorkflowConflict("simulated lease renewal conflict")

        self.runtime.renew_lease = renewal_fails
        result_box = []
        errors = []

        def execute():
            try:
                result_box.append(worker.run_once())
            except Exception as exc:
                errors.append(exc)

        thread = Thread(target=execute)
        thread.start()
        self.assertTrue(started.wait(1))
        time.sleep(0.15)
        release.set()
        thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(result_box, [])
        self.assertEqual(len(errors), 1)
        current = self.store.get(task_id)
        self.assertEqual(current.state, "running")
        self.assertIsNone(current.result)

    def test_worker_enforces_spec_runtime_deadline_and_discards_late_output(self):
        import threading
        import time
        from automation.agent_execution_spec import ExecutionBudget
        spec = AgentExecutionSpec(
            spec_id="runtime-budget-agent", role="code-review",
            instructions="Review safely.", approval_policy="not_requested",
            budget=ExecutionBudget(max_runtime_seconds=1),
        )
        self.runtime.register_agent_spec(actor_id="alice", spec=spec)
        task = self.runtime.enqueue(
            actor_id="alice", idempotency_key="runtime-budget-task",
            name="code-review", steps=[{
                "name": "review", "action": "emit", "argument": "inspect",
                "requires_approval": False,
            }], agent_execution_spec_id=spec.spec_id,
        )
        started = threading.Event()
        release = threading.Event()
        def slow_handler(_value):
            started.set()
            release.wait(3)
            return "late output"
        result = self.worker({"code-review": slow_handler}).run_once()
        self.assertTrue(started.is_set())
        self.assertEqual(result.task_id, task.task_id)
        self.assertEqual(result.state, "failed")
        self.assertEqual(result.error_code, "agent_runtime_budget_exceeded")
        release.set()
        time.sleep(0.05)
        persisted = self.store.get(task.task_id)
        self.assertEqual(persisted.state, "failed")
        self.assertIsNone(persisted.result)

    def test_worker_enforces_spec_text_budget(self):
        from automation.agent_execution_spec import ExecutionBudget
        spec = AgentExecutionSpec(
            spec_id="text-budget-agent", role="code-review",
            instructions="Review safely.", approval_policy="not_requested",
            budget=ExecutionBudget(max_tokens=4),
        )
        self.runtime.register_agent_spec(actor_id="alice", spec=spec)
        task = self.runtime.enqueue(
            actor_id="alice", idempotency_key="text-budget-task",
            name="code-review", steps=[{
                "name": "review", "action": "emit", "argument": "inspect",
                "requires_approval": False,
            }], agent_execution_spec_id=spec.spec_id,
        )
        called = []
        result = self.worker({"code-review": lambda value: called.append(value) or "ok"}).run_once()
        self.assertEqual(result.task_id, task.task_id)
        self.assertEqual(result.state, "failed")
        self.assertEqual(result.error_code, "agent_token_budget_exceeded")
        self.assertEqual(called, [])

    def test_worker_fails_closed_on_malformed_but_rehashed_spec(self):
        import hashlib
        import json
        spec = AgentExecutionSpec(
            spec_id="malformed-rehashed-agent",
            role="code-review",
            instructions="Review supplied content.",
            capabilities=("repository:read",),
            approval_policy="not_requested",
        )
        self.runtime.register_agent_spec(actor_id="alice", spec=spec)
        task = self.runtime.enqueue(
            actor_id="alice", idempotency_key="malformed-rehashed-task",
            name="code-review", steps=[{
                "name": "review", "action": "emit", "argument": "inspect",
                "requires_approval": False,
            }], agent_execution_spec_id=spec.spec_id,
        )
        with self.store._connect() as db:
            row = db.execute(
                "SELECT spec_json FROM agent_execution_specs WHERE spec_id=?",
                (spec.spec_id,),
            ).fetchone()
            malformed = json.loads(row["spec_json"])
            malformed["capabilities"] = "repository:read"
            encoded = json.dumps(
                malformed, ensure_ascii=False, sort_keys=True,
                separators=(",", ":"), allow_nan=False,
            )
            db.execute(
                "UPDATE agent_execution_specs SET spec_json=?, spec_digest=? WHERE spec_id=?",
                (encoded, hashlib.sha256(encoded.encode("utf-8")).hexdigest(), spec.spec_id),
            )
        called = []
        result = self.worker({
            "code-review": lambda value: called.append(value) or "must not execute",
        }).run_once()
        self.assertEqual(result.task_id, task.task_id)
        self.assertEqual(result.state, "failed")
        self.assertEqual(result.error_code, "agent_spec_integrity")
        self.assertEqual(called, [])

    def test_worker_enforces_explicit_handler_capability_grants(self):
        spec = AgentExecutionSpec(
            spec_id="capability-granted-agent",
            role="code-review",
            instructions="Review code using the approved repository reader.",
            capabilities=("repository:read",),
            approval_policy="not_requested",
        )
        self.runtime.register_agent_spec(actor_id="alice", spec=spec)
        task = self.runtime.enqueue(
            actor_id="alice", idempotency_key="capability-granted-task",
            name="code-review", steps=[{
                "name": "review", "action": "emit", "argument": "inspect",
                "requires_approval": False,
            }], agent_execution_spec_id=spec.spec_id,
        )
        called = []
        worker = SpecialistTaskWorker(
            self.runtime, worker_id="worker-capability-granted",
            service_actor_id="worker-service",
            handlers={"code-review": lambda value: called.append(value) or "reviewed"},
            handler_capabilities={"code-review": ("repository:read", "repository:metadata")},
        )
        result = worker.run_once()
        self.assertEqual(result.task_id, task.task_id)
        self.assertEqual(result.state, "succeeded")
        self.assertEqual(called, ["inspect"])

    def test_worker_denies_ungranted_agent_capability_before_handler(self):
        spec = AgentExecutionSpec(
            spec_id="capability-denied-agent",
            role="code-review",
            instructions="Review code using the approved repository writer.",
            capabilities=("repository:write",),
            approval_policy="not_requested",
        )
        self.runtime.register_agent_spec(actor_id="alice", spec=spec)
        task = self.runtime.enqueue(
            actor_id="alice", idempotency_key="capability-denied-task",
            name="code-review", steps=[{
                "name": "review", "action": "emit", "argument": "inspect",
                "requires_approval": False,
            }], agent_execution_spec_id=spec.spec_id,
        )
        called = []
        worker = self.worker({"code-review": lambda value: called.append(value) or "unsafe"})
        result = worker.run_once()
        self.assertEqual(result.task_id, task.task_id)
        self.assertEqual(result.state, "failed")
        self.assertEqual(result.error_code, "agent_capability_denied")
        self.assertEqual(called, [])

    def test_worker_denies_capabilities_when_no_grants_are_configured(self):
        spec = AgentExecutionSpec(
            spec_id="capability-no-grants-agent",
            role="code-review",
            instructions="Read repository metadata.",
            capabilities=("repository:read",),
            approval_policy="not_requested",
        )
        self.runtime.register_agent_spec(actor_id="alice", spec=spec)
        self.runtime.enqueue(
            actor_id="alice", idempotency_key="capability-no-grants-task",
            name="code-review", steps=[{
                "name": "review", "action": "emit", "argument": "inspect",
                "requires_approval": False,
            }], agent_execution_spec_id=spec.spec_id,
        )
        called = []
        result = self.worker({"code-review": lambda value: called.append(value) or "unsafe"}).run_once()
        self.assertEqual(result.state, "failed")
        self.assertEqual(result.error_code, "agent_capability_denied")
        self.assertEqual(called, [])

    def test_worker_fails_closed_when_bound_spec_digest_changes(self):
        spec = AgentExecutionSpec(
            spec_id="code-review-tamper",
            role="code-review",
            instructions="Review code safely.",
            approval_policy="not_requested",
            output_types=("text",),
        )
        self.runtime.register_agent_spec(actor_id="alice", spec=spec)
        task = self.runtime.enqueue(
            actor_id="alice", idempotency_key="spec-tamper-task",
            name="code-review", steps=[{
                "name": "review", "action": "emit", "argument": "inspect",
                "requires_approval": False,
            }], agent_execution_spec_id=spec.spec_id,
        )
        with self.store._connect() as db:
            db.execute(
                "UPDATE agent_execution_specs SET spec_digest=? WHERE spec_id=?",
                ("0" * 64, spec.spec_id),
            )
        result = self.worker({"code-review": lambda _: "must not run"}).run_once()
        # A corrupted persisted spec is made unclaimable by the scheduler;
        # the worker never receives it and therefore cannot execute it.
        self.assertIsNone(result)
        current = self.store.get(task.task_id)
        self.assertEqual(current.state, "queued")
        self.assertIsNone(current.result)

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

    def test_specialist_output_is_persisted_and_journaled_as_artifact(self):
        workspace_root = Path(self.tmp.name).resolve() / "specialist-artifacts"
        workspace_root.mkdir(mode=0o700)
        try:
            resolver = TaskWorkspaceResolver(workspace_root)
        except WorkspaceSecurityError as exc:
            self.skipTest(str(exc))
        self.runtime.artifact_store = TaskArtifactStore(
            resolver,
            authorize=lambda actor, action, task_id: self.runtime.is_authorized(
                actor, "task:artifact", task_id
            ),
        )
        task_id = self.create_task()
        result = self.worker({"code-review": lambda text: "reviewed: " + text}).run_once()
        reference = result.result["artifact_reference"]
        self.assertTrue(reference.startswith("yuki-artifact://sha256/"))
        events = self.runtime.events(actor_id="alice", task_id=task_id)
        artifact_events = [event for event in events if event.kind == "task.artifact"]
        self.assertEqual(len(artifact_events), 1)
        self.assertEqual(artifact_events[0].payload["reference"], reference)
        self.assertEqual(
            self.runtime.get_artifact(
                actor_id="alice", task_id=task_id, reference=reference,
            ),
            b"reviewed: Propose a safe patch",
        )

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
            def create_selected_adapter(self):
                return FakeProvider(output="provider result")

        handlers = build_handlers_from_provider_manager(
            Manager(), {"code-review": "coding", "qa-check": "testing"}
        )
        self.assertEqual(set(handlers), {"code-review", "qa-check"})
        self.assertEqual(handlers["code-review"]("Review this patch"), "provider result")

    def test_provider_preferences_route_each_specialist_independently(self):
        class RoutedManager:
            def __init__(self):
                self.calls = []
                self.selected = FakeProvider(output="selected")
                self.cloud = FakeProvider(output="cloud-routed")
            def create_selected_adapter(self):
                return self.selected
            def create_routed_adapter(self, *, required, preference, allow_cloud):
                self.calls.append((required, preference, allow_cloud))
                return self.cloud

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

    def test_provider_preference_failures_never_fall_back_to_selected_provider(self):
        class RoutedManager:
            def __init__(self):
                self.selected = FakeProvider(output="must-not-be-used")
            def create_selected_adapter(self):
                return self.selected
            def create_routed_adapter(self, **kwargs):
                raise RuntimeError("no eligible route")

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

    def test_provider_manager_role_map_fails_closed(self):
        class MissingManager:
            pass

        with self.assertRaises(TypeError):
            build_handlers_from_provider_manager(MissingManager(), {"agent": "coding"})
        class EmptyManager:
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

    def test_registered_parallel_pool_fences_and_retires_each_child_session(self):
        plan = self.coordinator.submit(
            actor_id="alice", idempotency_key="registered-parallel-plan",
            subtasks=[
                SubtaskSpec("first", "First registered review", ("coding",), requires_approval=False),
                SubtaskSpec("second", "Second registered review", ("coding",), requires_approval=False),
            ],
        )
        self.coordinator.advance(actor_id="alice", plan_id=plan.plan_id)
        worker = self.worker({"code-review": lambda text: "completed: " + text})
        registered = RegisteredSpecialistWorker(worker, self.registry)
        try:
            results = registered.run_concurrently(max_workers=2, max_tasks=2)
            self.assertEqual(len(results), 2)
            self.assertTrue(all(item.state == "succeeded" for item in results))
            records = self.registry.list_workers(limit=10)
            children = [item for item in records if item.worker_id.startswith("worker-a.pool-")]
            self.assertEqual(len(children), 2)
            self.assertTrue(all(item.status == "stopped" for item in children))
            self.assertTrue(all(item.metadata["parent_worker_id"] == "worker-a" for item in children))
        finally:
            registered.close()

    def test_registered_parallel_child_heartbeat_refreshes_during_long_handler(self):
        self.create_task()
        started = Event()

        def slow_handler(_argument):
            started.set()
            time.sleep(0.35)
            return "finished"

        worker = self.worker({"code-review": slow_handler})
        registered = RegisteredSpecialistWorker(
            worker, self.registry, heartbeat_interval=0.1,
        )
        try:
            results = registered.run_concurrently(max_workers=1, max_tasks=1)
            self.assertTrue(started.is_set())
            self.assertEqual(len(results), 1)
            child = next(
                item for item in self.registry.list_workers(limit=10)
                if item.worker_id.startswith("worker-a.pool-")
            )
            self.assertEqual(child.status, "stopped")
            self.assertGreater(child.last_seen_at, child.started_at)
        finally:
            registered.close()

    def test_parallel_child_claim_fails_when_parent_session_is_replaced(self):
        task_id = self.create_task()
        worker = self.worker({"code-review": lambda _: "must not execute"})
        worker._registry_session = ("worker-a", "parent-session")
        cleanup = []
        with self.assertRaisesRegex(RuntimeError, "parent session replaced"):
            worker.run_concurrently(
                max_workers=1,
                max_tasks=1,
                worker_session_factory=lambda worker_id: (worker_id, "child-session"),
                worker_session_cleanup=lambda worker_id, session: cleanup.append((worker_id, session)),
                worker_session_guard=lambda: (_ for _ in ()).throw(
                    RuntimeError("parent session replaced")
                ),
                worker_session_assertion=lambda _worker_id, _session: None,
                worker_session_heartbeat=lambda _worker_id, _session: None,
            )
        self.assertEqual(self.store.get(task_id).state, "queued")
        self.assertEqual(len(cleanup), 1)
        self.assertEqual(cleanup[0][0], cleanup[0][1][0])

    def test_registered_worker_cannot_start_parallel_pool_without_child_sessions(self):
        worker = self.worker({"code-review": lambda _: "result"})
        worker._registry_session = ("worker-a", "test-session")
        with self.assertRaisesRegex(RuntimeError, "individual sessions, a parent guard, and child heartbeats"):
            worker.run_concurrently(max_workers=2, max_tasks=2)

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

    def test_worker_rejects_nul_bytes_in_service_identities(self):
        with self.assertRaises(ValueError):
            SpecialistTaskWorker(
                self.runtime, worker_id="worker\x00admin",
                service_actor_id="worker-service", handlers={"code-review": lambda _: "ok"},
            )
        with self.assertRaises(ValueError):
            SpecialistTaskWorker(
                self.runtime, worker_id="worker-a",
                service_actor_id="worker-service\x00admin", handlers={"code-review": lambda _: "ok"},
            )

    def test_invalid_handler_registry_rejected(self):
        with self.assertRaises(ValueError):
            self.worker({})
        with self.assertRaises(ValueError):
            self.worker({"not valid": lambda _: "no"})


if __name__ == "__main__":
    unittest.main()
