import tempfile
import unittest
from pathlib import Path

from automation.agent_coordinator import (
    SpecialistAgent, SpecialistAgentCoordinator, SubtaskSpec,
)
from automation.specialist_worker import (
    SpecialistTaskWorker, build_provider_specialist_handlers,
)
from automation.task_runtime import TaskRuntime
from automation.workflow_store import WorkflowRunStore


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

    def test_provider_unavailable_fails_closed(self):
        handlers = build_provider_specialist_handlers(FakeProvider(available=False))
        with self.assertRaises(RuntimeError):
            handlers["reporting"]("Summarize the supplied facts")

    def test_invalid_handler_registry_rejected(self):
        with self.assertRaises(ValueError):
            self.worker({})
        with self.assertRaises(ValueError):
            self.worker({"not valid": lambda _: "no"})


if __name__ == "__main__":
    unittest.main()
