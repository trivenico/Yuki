import unittest
from unittest.mock import Mock

from automation.agent_coordinator import AgentPlan, PlannedSubtask, SpecialistAgentCoordinator
from core.intelligence_orchestrator import IntelligenceOrchestrator


class IntelligenceOrchestratorTests(unittest.TestCase):
    def setUp(self):
        self.coordinator = Mock(spec=SpecialistAgentCoordinator)
        self.coordinator.submit.return_value = AgentPlan(
            plan_id="plan-1", idempotency_key="request-1", created_at="now",
            subtasks=(PlannedSubtask(
                subtask_id="research", agent_id="researcher",
                description="Gather evidence", required_capabilities=("research",),
                required_permissions=(), depends_on=(), requires_approval=True,
                task_id=None, status="pending",
            ),),
        )
        self.orchestrator = IntelligenceOrchestrator(self.coordinator)

    def model(self, *, messages, tools, tool_choice):
        self.assertEqual(tools, [])
        self.assertEqual(tool_choice, "none")
        return {
            "tasks": [{
                "task_id": "research", "description": "Gather evidence",
                "required_capabilities": ["research"], "depends_on": [],
                "requires_approval": True,
            }]
        }

    def test_model_proposal_is_validated_and_submitted_for_routing(self):
        result = self.orchestrator.propose(
            actor_id="owner", request="Research this topic",
            idempotency_key="request-1", model=self.model,
        )
        self.assertEqual(result.plan_id, "plan-1")
        kwargs = self.coordinator.submit.call_args.kwargs
        self.assertEqual(kwargs["actor_id"], "owner")
        self.assertEqual(kwargs["subtasks"][0].required_capabilities, ("research",))
        self.assertTrue(kwargs["subtasks"][0].requires_approval)

    def test_accepts_provider_adapter_envelope_without_tool_calls(self):
        response = {
            "text": '{"tasks":[{"task_id":"research","description":"Gather evidence","required_capabilities":["research"],"depends_on":[],"requires_approval":true}]}',
            "tool_calls": {"tool_calls": []},
        }
        self.orchestrator.propose(
            actor_id="owner", request="Research this topic",
            idempotency_key="provider-envelope", model=lambda **kwargs: response,
        )
        self.assertEqual(self.coordinator.submit.call_args.kwargs["subtasks"][0].subtask_id, "research")

    def test_rejects_provider_tool_calls_during_planning(self):
        response = {"text": '{"tasks":[]}', "tool_calls": {"tool_calls": [
            {"id": "call_1", "name": "dangerous", "arguments": {}}
        ]}}
        with self.assertRaisesRegex(ValueError, "must_not_request_tools"):
            self.orchestrator.propose(
                actor_id="owner", request="Research this topic",
                idempotency_key="provider-tool-call", model=lambda **kwargs: response,
            )
        self.coordinator.submit.assert_not_called()

    def test_rejects_extra_fields_and_invalid_dependency_graph(self):
        bad_responses = [
            {"tasks": [], "tool_calls": []},
            {"tasks": [{
                "task_id": "one", "description": "Do work",
                "required_capabilities": ["research"], "depends_on": [],
                "requires_approval": True, "agent_id": "admin",
            }]},
            {"tasks": [
                {"task_id": "one", "description": "First", "required_capabilities": ["research"], "depends_on": ["two"], "requires_approval": True},
                {"task_id": "two", "description": "Second", "required_capabilities": ["research"], "depends_on": ["one"], "requires_approval": True},
            ]},
        ]
        for response in bad_responses:
            with self.subTest(response=response), self.assertRaises(ValueError):
                self.orchestrator.propose(
                    actor_id="owner", request="Do work",
                    idempotency_key="bad", model=lambda **kwargs: response,
                )
        self.coordinator.submit.assert_not_called()

    def test_model_cannot_silently_remove_approval(self):
        response = {"tasks": [{
            "task_id": "write", "description": "Change production settings",
            "required_capabilities": ["coding"], "depends_on": [],
            "requires_approval": False,
        }]}
        result = self.orchestrator.propose(
            actor_id="owner", request="Change production settings",
            idempotency_key="write", model=lambda **kwargs: response,
        )
        self.assertTrue(self.coordinator.submit.call_args.kwargs["subtasks"][0].requires_approval)

    def test_dispatch_and_read_delegate_to_authorized_coordinator(self):
        self.orchestrator.dispatch_ready(actor_id="owner", plan_id="plan-1")
        self.coordinator.advance.assert_called_once_with(actor_id="owner", plan_id="plan-1")
        self.orchestrator.get_plan(actor_id="owner", plan_id="plan-1")
        self.coordinator.get_plan.assert_called_once_with(actor_id="owner", plan_id="plan-1")

    def test_invalid_request_and_model_are_rejected(self):
        with self.assertRaises(ValueError):
            self.orchestrator.propose(actor_id="owner", request=" ", idempotency_key="x", model=self.model)
        with self.assertRaises(TypeError):
            self.orchestrator.propose(actor_id="owner", request="valid", idempotency_key="x", model=None)


if __name__ == "__main__":
    unittest.main()
