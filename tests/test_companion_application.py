import tempfile
import unittest
from pathlib import Path

from companion.application import CompanionApplicationService
from companion.conversation import ConversationError
from companion.memory import MemoryStore
from companion.memory_consent import CompanionMemory
from automation.agent_coordinator import SpecialistAgent, SpecialistAgentCoordinator
from automation.task_runtime import TaskRuntime
from automation.workflow_store import WorkflowConflict, WorkflowRunStore


class FakeProvider:
    def __init__(self, answers=None):
        self.answers = list(answers or ["hello", "second"])
        self.prompts = []

    def is_available(self):
        return True

    def generate_chat(self, system, messages):
        self.prompts.append((system, messages))
        return self.answers.pop(0)


class CompanionApplicationServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.provider = FakeProvider()
        self.memory = CompanionMemory(MemoryStore(str(Path(self.temp.name) / "memory.json")))
        self.app = CompanionApplicationService(self.provider, memory=self.memory)

    def test_memory_is_not_injected_until_session_context_consent(self):
        self.memory.enable()
        saved = self.memory.remember("I prefer concise replies", category="preference")
        self.app.respond("hello there")
        self.assertNotIn("I prefer concise replies", self.provider.prompts[0][0])
        self.memory.enable_context()
        self.app.respond("Please use concise replies")
        self.assertIn("I prefer concise replies", self.provider.prompts[1][0])

    def test_memory_retrieval_failure_fails_closed_before_provider_request(self):
        class BrokenMemory(CompanionMemory):
            def context_entries(self, *, query="", limit=10):
                raise OSError("private database details")

        memory = BrokenMemory(MemoryStore(str(Path(self.temp.name) / "broken-memory.json")))
        app = CompanionApplicationService(self.provider, memory=memory)
        with self.assertRaises(ConversationError):
            app.respond("hello")
        self.assertEqual(self.provider.prompts, [])

    def test_role_and_reset_are_shared_service_operations(self):
        self.app.set_role("developer")
        self.app.respond("review this")
        self.assertIn("Current interaction role: developer", self.provider.prompts[0][0])
        self.app.reset_conversation()
        self.assertEqual(self.app.session.history(), ())

    def test_agent_proposal_is_separate_from_dispatch_and_execution(self):
        import json

        class PlanningProvider(FakeProvider):
            def generate_chat(self, system, messages):
                self.prompts.append((system, messages))
                return json.dumps({"tasks": [{
                    "task_id": "research",
                    "description": "Research the requested topic",
                    "required_capabilities": ["research"],
                    "depends_on": [],
                }]})

        provider = PlanningProvider()
        db_path = str(Path(self.temp.name) / "agent-tasks.sqlite3")
        store = WorkflowRunStore(db_path)
        runtime = TaskRuntime(
            store,
            authorize=lambda actor, action, task: actor == "alice" and action in {
                "task:create", "task:read", "task:list", "task:cancel",
            },
        )
        coordinator = SpecialistAgentCoordinator(
            runtime, db_path,
            [SpecialistAgent("research", "Research specialist", frozenset({"research"}), "emit")],
            authorize=lambda actor, action, plan: actor == "alice" and action in {
                "agent:plan", "agent:dispatch", "agent:read", "agent:cancel",
            },
        )
        memory = CompanionMemory(MemoryStore(str(Path(self.temp.name) / "agent-memory.json")))
        app = CompanionApplicationService(
            provider, memory=memory, agent_coordinator=coordinator,
        )
        self.assertIs(app.task_runtime, runtime)
        self.assertEqual(app.list_specialists(actor_id="alice")[0]["agent_id"], "research")

        proposed = app.propose_agent_task(
            actor_id="alice", idempotency_key="companion-research",
            goal="Research the requested topic",
        )
        self.assertIsNone(proposed.subtasks[0].task_id)
        self.assertEqual(proposed.subtasks[0].status, "pending")
        self.assertTrue(proposed.subtasks[0].requires_approval)
        reviewed = app.get_agent_plan(actor_id="alice", plan_id=proposed.plan_id)
        self.assertEqual(reviewed.plan_id, proposed.plan_id)
        self.assertEqual(store.list_runs(limit=10), [])

        queued = app.dispatch_agent_plan(actor_id="alice", plan_id=proposed.plan_id)
        self.assertIsNotNone(queued.subtasks[0].task_id)
        task = runtime.get(actor_id="alice", task_id=queued.subtasks[0].task_id)
        self.assertTrue(task.steps[0]["requires_approval"])
        self.assertNotEqual(task.state, "succeeded")
        cancelled_plan = app.cancel_agent_plan(
            actor_id="alice", plan_id=queued.plan_id,
        )
        self.assertEqual(cancelled_plan.subtasks[0].status, "cancelled")
        self.assertEqual(runtime.get(actor_id="alice", task_id=task.task_id).state, "cancelled")

    def test_specialist_catalog_requires_coordinator_and_registry_permission(self):
        with self.assertRaises(ConversationError):
            self.app.list_specialists(actor_id="alice")

        db_path = str(Path(self.temp.name) / "specialist-list.sqlite3")
        runtime = TaskRuntime(WorkflowRunStore(db_path))
        coordinator = SpecialistAgentCoordinator(
            runtime, db_path,
            [SpecialistAgent("research", "Research", frozenset({"research"}), "emit")],
            authorize=lambda actor, action, plan: actor == "alice" and action == "agent:plan",
        )
        app = CompanionApplicationService(self.provider, agent_coordinator=coordinator)
        with self.assertRaises(PermissionError):
            app.list_specialists(actor_id="alice")

    def test_agent_operations_fail_closed_when_coordinator_missing(self):
        with self.assertRaises(ConversationError):
            self.app.propose_agent_task(
                actor_id="alice", idempotency_key="no-coordinator",
                goal="Research this",
            )
        with self.assertRaises(ConversationError):
            self.app.dispatch_agent_plan(actor_id="alice", plan_id="unknown")

    def test_companion_reads_redacted_task_progress_and_cancels_explicitly(self):
        db_path = str(Path(self.temp.name) / "companion-task-runtime.sqlite3")
        store = WorkflowRunStore(db_path)
        runtime = TaskRuntime(
            store,
            authorize=lambda actor, action, task: actor == "alice" and action in {
                "task:create", "task:read", "task:cancel",
            },
        )
        task = runtime.enqueue(
            actor_id="alice", idempotency_key="companion-task-progress",
            name="research", steps=[{
                "name": "collect", "action": "emit", "argument": "private prompt",
                "requires_approval": False,
            }],
        )
        app = CompanionApplicationService(
            self.provider, memory=self.memory, task_runtime=runtime,
        )
        updates = app.task_progress(
            actor_id="alice", task_id=task.task_id, after_sequence=0,
        )
        self.assertEqual(len(updates), 1)
        self.assertEqual(updates[0].message, "Started task: research")
        self.assertNotIn("private prompt", repr(updates))

        cancelled = app.cancel_task(
            actor_id="alice", task_id=task.task_id,
            expected_version=task.version,
        )
        self.assertEqual(cancelled.state, "cancelled")
        final_updates = app.task_progress(
            actor_id="alice", task_id=task.task_id,
            after_sequence=updates[-1].sequence,
        )
        self.assertEqual([item.kind for item in final_updates], ["task.cancelled"])

    def test_companion_lists_only_redacted_task_summaries(self):
        db_path = str(Path(self.temp.name) / "companion-task-list.sqlite3")
        store = WorkflowRunStore(db_path)
        runtime = TaskRuntime(
            store,
            authorize=lambda actor, action, task: actor == "alice" and action in {
                "task:create", "task:read", "task:list",
            },
        )
        runtime.enqueue(
            actor_id="alice", idempotency_key="list-task",
            name="private workflow title",
            steps=[{"name": "step", "action": "emit", "argument": "private input",
                    "requires_approval": False}],
        )
        app = CompanionApplicationService(
            self.provider, memory=self.memory, task_runtime=runtime,
        )
        summaries = app.list_tasks(actor_id="alice", limit=10)
        self.assertEqual(len(summaries), 1)
        self.assertEqual(summaries[0].name, "private workflow title")
        self.assertFalse(hasattr(summaries[0], "steps"))
        self.assertFalse(hasattr(summaries[0], "result"))

    def test_companion_task_list_requires_task_list_permission(self):
        db_path = str(Path(self.temp.name) / "companion-task-list-denied.sqlite3")
        runtime = TaskRuntime(
            WorkflowRunStore(db_path),
            authorize=lambda actor, action, task: actor == "alice" and action == "task:read",
        )
        app = CompanionApplicationService(
            self.provider, memory=self.memory, task_runtime=runtime,
        )
        with self.assertRaises(PermissionError):
            app.list_tasks(actor_id="alice")

    def test_companion_retry_creates_fresh_task_after_failure(self):
        db_path = str(Path(self.temp.name) / "companion-task-retry.sqlite3")
        store = WorkflowRunStore(db_path)
        runtime = TaskRuntime(
            store,
            authorize=lambda actor, action, task: actor == "alice" and action in {
                "task:create", "task:fail", "task:retry", "task:read",
            },
        )
        original = runtime.enqueue(
            actor_id="alice", idempotency_key="original-failed-task",
            name="retryable task", steps=[{
                "name": "run", "action": "emit", "argument": "safe",
                "requires_approval": False,
            }],
        )
        claimed = store.claim_next(worker_id="test-worker")
        self.assertIsNotNone(claimed)
        failed = runtime.fail(
            actor_id="alice", task_id=original.task_id,
            expected_version=claimed.version, worker_id="test-worker",
            error_code="test_failure",
        )
        app = CompanionApplicationService(
            self.provider, memory=self.memory, task_runtime=runtime,
        )
        with self.assertRaises(WorkflowConflict):
            app.retry_task(
                actor_id="alice", task_id=failed.task_id,
                expected_version=failed.version - 1,
            )
        retried = app.retry_task(
            actor_id="alice", task_id=failed.task_id,
            expected_version=failed.version,
        )
        self.assertNotEqual(retried.task_id, failed.task_id)
        self.assertEqual(retried.state, "queued")
        self.assertEqual(retried.name, failed.name)

    def test_companion_retry_requires_retry_permission(self):
        db_path = str(Path(self.temp.name) / "companion-task-retry-denied.sqlite3")
        runtime = TaskRuntime(
            WorkflowRunStore(db_path),
            authorize=lambda actor, action, task: actor == "alice" and action == "task:read",
        )
        app = CompanionApplicationService(
            self.provider, memory=self.memory, task_runtime=runtime,
        )
        with self.assertRaises(PermissionError):
            app.retry_task(actor_id="alice", task_id="missing", expected_version=0)

    def test_companion_task_controls_fail_closed_without_runtime(self):
        with self.assertRaises(ConversationError):
            self.app.task_progress(actor_id="alice", task_id="missing")
        with self.assertRaises(ConversationError):
            self.app.cancel_task(
                actor_id="alice", task_id="missing", expected_version=0,
            )

    def test_service_rejects_mismatched_provider_session(self):
        from companion.conversation import ConversationSession
        other = FakeProvider()
        with self.assertRaises(ValueError):
            CompanionApplicationService(
                self.provider, memory=self.memory, session=ConversationSession(other),
            )


if __name__ == "__main__":
    unittest.main()
