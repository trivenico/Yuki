import tempfile
import unittest
from pathlib import Path

from automation.agent_coordinator import SpecialistAgent, SpecialistAgentCoordinator
from automation.specialist_worker import SpecialistTaskWorker
from automation.task_runtime import TaskRuntime
from automation.workflow_approval import AuthenticatedPrincipal, WorkflowApprovalService
from automation.workflow_store import WorkflowRunStore
from automation.worker_supervisor import WorkerSupervisor
from core.agent_execution_service import AgentExecutionService
from core.conversation_agent_bridge import ConversationAgentBridge
from core.conversation_runtime import ConversationRuntime
from core.conversation_store import ConversationStore
from core.model_agent_planner import ModelAgentPlanner


class FakeProvider:
    def __init__(self):
        self.calls = 0

    def is_available(self):
        return True

    def generate(self, _prompt):
        self.calls += 1
        return (
            '{"subtasks":[{"subtask_id":"research","description":"Review the request",'
            '"required_capabilities":["research"],"depends_on":[],'
            '"required_permissions":["material:read"],"requires_approval":true}]}'
        )


class ConversationAgentBridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        workflow_store = WorkflowRunStore(str(root / "workflow.sqlite3"))
        grants = {
            "task:create", "task:read", "task:progress", "task:artifact",
            "task:complete", "task:lease:renew", "task:recover", "task:fail",
            "task:cancel",
        }
        runtime = TaskRuntime(
            workflow_store,
            authorize=lambda actor, action, _task: (
                actor == "alice" and action in {"task:create", "task:read"}
            ) or (actor == "worker-service" and action in grants),
        )
        self.coordinator = SpecialistAgentCoordinator(
            runtime, str(root / "plans.sqlite3"),
            [SpecialistAgent(
                "researcher", "Research specialist", frozenset({"research"}),
                "emit", frozenset({"material:read"}),
            )],
            authorize=lambda actor, action, _plan: actor == "alice" and action in {
                "agent:plan", "agent:dispatch", "agent:read"
            },
        )
        self.planner_provider = FakeProvider()
        planner = ModelAgentPlanner(self.planner_provider, self.coordinator)
        approvals = WorkflowApprovalService(
            workflow_store,
            principal_resolver=lambda context: {
                "reviewer": AuthenticatedPrincipal(
                    "reviewer", "trusted-reviewer",
                    frozenset({"workflow:approve"}),
                )
            }[context],
        )
        worker = SpecialistTaskWorker(
            runtime, worker_id="worker", service_actor_id="worker-service",
            handlers={"researcher": lambda value: "Reviewed " + value},
        )
        self.agent_service = AgentExecutionService(
            planner, self.coordinator, approvals,
            WorkerSupervisor(runtime, worker, max_tasks_per_cycle=3),
        )
        self.conversation_store = ConversationStore(root / "conversations.sqlite3")
        self.conversation_provider = FakeProvider()
        conversation_runtime = ConversationRuntime(
            self.conversation_store, self.conversation_provider,
        )
        self.bridge = ConversationAgentBridge(conversation_runtime, self.agent_service)

    def test_conversation_creates_durable_approval_gated_agent_plan(self):
        result = self.bridge.submit_agent_turn(
            actor_id="alice", workspace_id="workspace-a",
            user_message="Research this request", request_id="request-001",
        )
        self.assertTrue(result.ok)
        self.assertIsNotNone(result.plan_id)
        self.assertEqual(result.plan_status, "queued")
        self.assertIn("human approval required", result.message)
        self.assertEqual(self.planner_provider.calls, 1)
        messages = self.conversation_store.list_messages("workspace-a", result.session_id)
        self.assertEqual([item["role"] for item in messages], ["user", "assistant"])
        self.assertEqual(self.conversation_store.list_turns("workspace-a", result.session_id)[0]["status"], "completed")
        plan = self.agent_service.get_plan(actor_id="alice", plan_id=result.plan_id)
        task = plan.subtasks[0]
        self.assertTrue(task.requires_approval)
        blocked = self.agent_service.run_worker_cycle()
        self.assertEqual(blocked.processed_count, 0)

    def test_completed_request_replay_does_not_create_second_plan(self):
        first = self.bridge.submit_agent_turn(
            actor_id="alice", workspace_id="workspace-a",
            user_message="Research this request", request_id="stable-request",
        )
        replay = self.bridge.submit_agent_turn(
            actor_id="alice", workspace_id="workspace-a",
            user_message="Research this request", request_id="stable-request",
        )
        self.assertTrue(first.ok and replay.ok)
        self.assertTrue(replay.replayed)
        self.assertEqual(first.plan_id, replay.plan_id if replay.plan_id else first.plan_id)
        self.assertEqual(self.planner_provider.calls, 1)
        self.assertEqual(len(self.conversation_store.list_messages("workspace-a", first.session_id)), 2)

    def test_untrusted_actor_is_rejected_before_plan_submission(self):
        result = self.bridge.submit_agent_turn(
            actor_id="mallory", workspace_id="workspace-a",
            user_message="Research this request", request_id="unauthorized",
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.error_code, "agent_plan_failed")
        self.assertEqual(self.planner_provider.calls, 0)


if __name__ == "__main__":
    unittest.main()
