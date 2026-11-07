import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from automation.engineering_director import EngineeringDirector, EngineeringProposal
from automation.agent_coordinator import SpecialistAgentCoordinator


class EngineeringDirectorTests(unittest.TestCase):
    def setUp(self):
        self.coordinator = object.__new__(SpecialistAgentCoordinator)
        self.coordinator.submit = Mock()
        self.coordinator.agents = {
            "coding": SimpleNamespace(agent_id="coding", enabled=True, capabilities=frozenset({"coding"})),
            "testing": SimpleNamespace(agent_id="testing", enabled=True, capabilities=frozenset({"testing"})),
        }
        self.director = EngineeringDirector(self.coordinator)

    def test_proposal_is_explainable_and_not_submitted(self):
        proposal = self.director.propose(
            "Implement Python code and add unit tests",
            explicit_task_type="coding",
        )
        self.assertIsInstance(proposal, EngineeringProposal)
        self.assertTrue(proposal.requires_operator_approval)
        self.assertEqual(len(proposal.tasks), 1)
        self.assertEqual(proposal.tasks[0].required_capabilities, ("coding",))
        self.assertTrue(proposal.tasks[0].acceptance_criteria)
        self.coordinator.submit.assert_not_called()

    def test_missing_specialist_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "no enabled specialist"):
            self.director.propose("Inspect a screenshot", explicit_task_type="vision")

    def test_submission_requires_explicit_approval(self):
        proposal = self.director.propose("Implement Python code", explicit_task_type="coding")
        with self.assertRaises(PermissionError):
            self.director.submit_approved(
                proposal, actor_id="operator", idempotency_key="request-1", approved=False
            )
        self.coordinator.submit.assert_not_called()

    def test_approved_submission_uses_coordinator(self):
        proposal = self.director.propose("Implement Python code", explicit_task_type="coding")
        expected = object()
        self.coordinator.submit.return_value = expected
        result = self.director.submit_approved(
            proposal, actor_id="operator", idempotency_key="request-2", approved=True
        )
        self.assertIs(result, expected)
        kwargs = self.coordinator.submit.call_args.kwargs
        self.assertEqual(kwargs["actor_id"], "operator")
        self.assertEqual(kwargs["idempotency_key"], "request-2")
        self.assertTrue(kwargs["subtasks"][0].requires_approval)


if __name__ == "__main__":
    unittest.main()
