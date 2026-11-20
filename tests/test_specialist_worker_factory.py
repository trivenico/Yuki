import tempfile
import unittest
from pathlib import Path

from automation.agent_coordinator import SpecialistAgent, SpecialistAgentCoordinator, SubtaskSpec
from automation.specialist_worker_factory import build_registered_specialist_worker
from automation.task_runtime import TaskRuntime
from automation.workflow_store import WorkflowRunStore


class FakeProvider:
    def is_available(self):
        return True

    def generate(self, prompt):
        return "bounded specialist result"


class FakeProviderManager:
    def create_selected_adapter(self):
        return FakeProvider()


class SpecialistWorkerFactoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        db = str(Path(self.tmp.name) / "yuki.sqlite3")
        self.runtime = TaskRuntime(
            WorkflowRunStore(db),
            authorize=lambda actor, action, task: actor in {"owner", "worker-service"},
        )
        self.coordinator = SpecialistAgentCoordinator(
            self.runtime, db,
            [
                SpecialistAgent(
                    "reviewer", "Code review specialist",
                    frozenset({"code-reviewer"}), "emit",
                ),
                SpecialistAgent(
                    "disabled", "Disabled specialist",
                    frozenset({"test-engineer"}), "emit", enabled=False,
                ),
                SpecialistAgent(
                    "legacy", "Legacy specialist",
                    frozenset({"coding"}), "emit",
                ),
            ],
            authorize=lambda actor, action, resource: (
                actor == "owner" and action in {"agent:read", "agent:plan", "agent:dispatch"}
            ),
        )

    def build(self, roles):
        return build_registered_specialist_worker(
            coordinator=self.coordinator,
            runtime=self.runtime,
            actor_id="owner",
            worker_id="worker-1",
            service_actor_id="worker-service",
            provider_manager=FakeProviderManager(),
            agent_roles=roles,
        )

    def test_builds_worker_for_enabled_catalog_backed_agent(self):
        worker = self.build({"reviewer": "coding"})
        self.assertEqual(set(worker.handlers), {"reviewer"})
        self.assertEqual(worker.handlers["reviewer"]("Review this"), "bounded specialist result")

    def test_rejects_unregistered_agent(self):
        with self.assertRaises(PermissionError):
            self.build({"not-registered": "coding"})

    def test_rejects_disabled_agent(self):
        with self.assertRaises(PermissionError):
            self.build({"disabled": "testing"})

    def test_rejects_agent_without_stable_catalog_capability(self):
        with self.assertRaises(ValueError):
            self.build({"legacy": "coding"})

    def test_end_to_end_approved_plan_runs_through_registered_worker(self):
        plan = self.coordinator.submit(
            actor_id="owner",
            idempotency_key="factory-e2e",
            subtasks=(
                SubtaskSpec(
                    "review", "Review the supplied patch",
                    ("code-reviewer",), requires_approval=False,
                ),
            ),
        )
        dispatched = self.coordinator.advance(actor_id="owner", plan_id=plan.plan_id)
        self.assertEqual(dispatched.subtasks[0].status, "queued")
        worker = self.build({"reviewer": "coding"})
        completed = worker.run_once()
        self.assertIsNotNone(completed)
        self.assertEqual(completed.state, "succeeded")
        self.assertEqual(completed.result["specialist"], "reviewer")
        self.assertEqual(completed.result["output"], "bounded specialist result")

    def test_registry_read_authorization_is_required(self):
        with self.assertRaises(PermissionError):
            build_registered_specialist_worker(
                coordinator=self.coordinator,
                runtime=self.runtime,
                actor_id="intruder",
                worker_id="worker-1",
                service_actor_id="worker-service",
                provider_manager=FakeProviderManager(),
                agent_roles={"reviewer": "coding"},
            )


if __name__ == "__main__":
    unittest.main()
