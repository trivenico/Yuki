import unittest
from unittest.mock import Mock

from automation.agent_coordinator import AgentPlan, PlannedSubtask, SpecialistAgentCoordinator
from automation.cognitive_agent_adapter import CognitiveAgentAdapter
from core.cognitive_context import CognitiveBrief
from core.planner import Plan, PlanTask


def make_brief(tasks):
    return CognitiveBrief(
        goal="Research and implement a local model",
        identity_revision=None,
        identity_profile=(),
        memory_ids=(),
        memory_context="",
        plan=Plan("Research and implement a local model", ()),
        specialist_tasks=tuple(tasks),
        warnings=(),
    )


class CognitiveAgentAdapterTests(unittest.TestCase):
    def setUp(self):
        self.coordinator = Mock(spec=SpecialistAgentCoordinator)
        self.adapter = CognitiveAgentAdapter(self.coordinator)

    def test_maps_dependencies_approval_and_no_permission_requests(self):
        brief = make_brief((
            PlanTask("research", "Research local model options", ("research",)),
            PlanTask("implementation", "Implement approved integration", ("coding",), ("research",), True),
            PlanTask("summary", "Summarize verified results", ("reporting",), ("research", "implementation"), False),
        ))
        specs = self.adapter.to_subtask_specs(brief)
        self.assertEqual([item.subtask_id for item in specs], ["research", "implementation", "summary"])
        self.assertEqual(specs[1].depends_on, ("research",))
        self.assertTrue(specs[1].requires_approval)
        self.assertFalse(specs[2].requires_approval)
        self.assertTrue(all(item.required_permissions == () for item in specs))

    def test_persists_proposal_without_dispatch(self):
        brief = make_brief((PlanTask("research", "Research options", ("research",)),))
        expected = AgentPlan("plan-1", "request-1", "2026-11-03T00:00:00+00:00", (
            PlannedSubtask("research", "researcher", "Research options", ("research",), (), (), True, None, "pending"),
        ))
        self.coordinator.submit.return_value = expected
        result = self.adapter.submit_proposal(actor_id="verified-owner", idempotency_key="request-1", brief=brief)
        self.assertEqual(result, expected)
        self.coordinator.submit.assert_called_once()
        self.coordinator.advance.assert_not_called()

    def test_revalidates_malformed_brief(self):
        brief = make_brief((PlanTask("research", "Research", ("Research",)),))
        with self.assertRaises(ValueError):
            self.adapter.to_subtask_specs(brief)
        self.coordinator.submit.assert_not_called()

    def test_rejects_invalid_actor_and_idempotency_key(self):
        brief = make_brief((PlanTask("research", "Research", ("research",)),))
        with self.assertRaises(ValueError):
            self.adapter.submit_proposal(actor_id=" ", idempotency_key="key", brief=brief)
        with self.assertRaises(ValueError):
            self.adapter.submit_proposal(actor_id="owner", idempotency_key="", brief=brief)
        self.coordinator.submit.assert_not_called()

    def test_rejects_non_cognitive_input(self):
        with self.assertRaises(TypeError):
            self.adapter.to_subtask_specs({"goal": "not a brief"})


if __name__ == "__main__":
    unittest.main()
