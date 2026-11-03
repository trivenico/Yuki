import tempfile
import unittest
from pathlib import Path

from automation.agent_coordinator import SpecialistAgent, SpecialistAgentCoordinator, SubtaskSpec
from automation.agent_message_bus import AgentMessageBus
from automation.specialist_supervisor import SpecialistSupervisor
from automation.task_runtime import TaskRuntime
from automation.workflow_store import WorkflowRunStore


class SpecialistSupervisorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        db = str(Path(self.tmp.name) / "yuki.sqlite3")
        self.runtime = TaskRuntime(
            WorkflowRunStore(db),
            authorize=lambda actor, action, resource: actor == "alice" and action in {
                "task:create", "task:read", "task:cancel"
            },
        )
        self.coordinator = SpecialistAgentCoordinator(
            self.runtime, db,
            [
                SpecialistAgent("research", "Research", frozenset({"research"}), "emit"),
                SpecialistAgent("testing", "Testing", frozenset({"testing"}), "emit"),
            ],
            authorize=lambda actor, action, resource: actor == "alice" and action in {
                "agent:plan", "agent:dispatch", "agent:read", "agent:cancel"
            },
        )
        def authorize(actor, action, resource):
            if action == "agent:message:send":
                return actor == "supervisor"
            if action == "agent:message:receive":
                return actor in {"research", "testing"}
            if action == "agent:message:read":
                return actor in {"supervisor", "research", "testing"}
            return False
        self.bus = AgentMessageBus(
            str(Path(self.tmp.name) / "messages.sqlite3"),
            registered_agent_ids=("supervisor", "research", "testing"),
            authorize=authorize,
        )
        self.supervisor = SpecialistSupervisor(
            supervisor_id="supervisor", coordinator=self.coordinator,
            message_bus=self.bus,
        )

    def make_plan(self):
        return self.supervisor.create_plan(
            actor_id="alice", idempotency_key="supervisor-plan-1",
            subtasks=(
                SubtaskSpec("collect", "Collect evidence", ("research",)),
                SubtaskSpec("verify", "Verify evidence", ("testing",), depends_on=("collect",)),
            ),
        )

    def test_create_plan_routes_through_existing_coordinator(self):
        plan = self.make_plan()
        self.assertEqual({s.agent_id for s in plan.subtasks}, {"research", "testing"})
        self.assertTrue(all(s.status == "pending" for s in plan.subtasks))

    def test_assignment_notifications_are_durable_and_idempotent(self):
        plan = self.make_plan()
        first = self.supervisor.notify_assignments(actor_id="alice", plan_id=plan.plan_id)
        second = self.supervisor.notify_assignments(actor_id="alice", plan_id=plan.plan_id)
        self.assertEqual(first, second)
        self.assertEqual(len(self.bus.inbox(actor_id="research")), 1)
        self.assertEqual(len(self.bus.inbox(actor_id="testing")), 1)

    def test_notification_failure_does_not_bypass_message_bus_acl(self):
        denied_bus = AgentMessageBus(
            str(Path(self.tmp.name) / "denied.sqlite3"),
            registered_agent_ids=("supervisor", "research", "testing"),
            authorize=lambda *_: False,
        )
        supervisor = SpecialistSupervisor(
            supervisor_id="supervisor", coordinator=self.coordinator,
            message_bus=denied_bus,
        )
        plan = self.make_plan()
        with self.assertRaises(PermissionError):
            supervisor.notify_assignments(actor_id="alice", plan_id=plan.plan_id)

    def test_dispatch_delegates_to_existing_coordinator(self):
        plan = self.make_plan()
        result = self.supervisor.dispatch(actor_id="alice", plan_id=plan.plan_id)
        by_id = {s.subtask_id: s for s in result.subtasks}
        self.assertIsNotNone(by_id["collect"].task_id)
        self.assertIsNone(by_id["verify"].task_id)

    def test_status_and_cancel_delegate_to_coordinator(self):
        plan = self.make_plan()
        self.supervisor.dispatch(actor_id="alice", plan_id=plan.plan_id)
        cancelled = self.supervisor.cancel(actor_id="alice", plan_id=plan.plan_id)
        self.assertTrue(all(s.status == "cancelled" for s in cancelled.subtasks))
        self.assertEqual(self.supervisor.status(actor_id="alice", plan_id=plan.plan_id), cancelled)

    def test_coordinator_authorization_remains_authoritative(self):
        with self.assertRaises(PermissionError):
            self.supervisor.create_plan(
                actor_id="mallory", idempotency_key="unauthorized",
                subtasks=(SubtaskSpec("collect", "Collect evidence", ("research",)),),
            )

    def test_supervisor_must_be_registered_on_message_bus(self):
        bus = AgentMessageBus(
            str(Path(self.tmp.name) / "missing-supervisor.sqlite3"),
            registered_agent_ids=("research", "testing"), authorize=lambda *_: True,
        )
        with self.assertRaises(ValueError):
            SpecialistSupervisor(
                supervisor_id="supervisor", coordinator=self.coordinator, message_bus=bus,
            )


if __name__ == "__main__":
    unittest.main()
