"""Tests for deterministic, non-executing agent capability routing."""
import unittest

from core.agent_orchestrator import (
    AgentOrchestrator,
    AgentRegistry,
    AgentSpec,
    CapabilityUnavailable,
    DuplicateAgentError,
)
from core.planner import PlanTask


class AgentOrchestratorTests(unittest.TestCase):
    def setUp(self):
        self.registry = AgentRegistry([
            AgentSpec("research_basic", "research_skill", ("research",)),
            AgentSpec("research_plus", "research_plus_skill", ("research", "reporting")),
            AgentSpec("code_agent", "code_skill", ("coding", "testing")),
        ])
        self.orchestrator = AgentOrchestrator(self.registry)

    def test_routes_to_narrowest_capability_match(self):
        routed = self.orchestrator.route([
            PlanTask("research", "Gather sources", ("research",)),
        ])
        self.assertEqual(routed[0].agent_id, "research_basic")
        self.assertEqual(routed[0].skill_name, "research_skill")

    def test_routes_all_tasks_and_preserves_dependencies(self):
        routed = self.orchestrator.route([
            PlanTask("research", "Gather evidence", ("research",)),
            PlanTask("implementation", "Build feature", ("coding",), ("research",)),
            PlanTask("verification", "Test feature", ("testing",), ("implementation",)),
        ])
        by_id = {item.task_id: item for item in routed}
        self.assertEqual(by_id["implementation"].depends_on, ("research",))
        self.assertEqual(by_id["verification"].depends_on, ("implementation",))
        self.assertEqual(by_id["implementation"].agent_id, "code_agent")

    def test_unavailable_capability_fails_closed(self):
        with self.assertRaises(CapabilityUnavailable):
            self.orchestrator.route([
                PlanTask("deploy", "Deploy production", ("production_deploy",)),
            ])

    def test_duplicate_agent_ids_are_rejected(self):
        with self.assertRaises(DuplicateAgentError):
            self.registry.register(
                AgentSpec("research_basic", "other_skill", ("research",))
            )

    def test_invalid_dag_is_rejected_before_routing(self):
        with self.assertRaises(ValueError):
            self.orchestrator.route([
                PlanTask("a", "First", ("research",), ("b",)),
                PlanTask("b", "Second", ("research",), ("a",)),
            ])

    def test_routing_does_not_execute_skill_code(self):
        calls = []

        class NeverExecute:
            def execute(self, *_args, **_kwargs):
                calls.append("executed")

        routed = self.orchestrator.route([
            PlanTask("research", "Gather sources", ("research",)),
        ])
        self.assertEqual(len(routed), 1)
        self.assertEqual(calls, [])

    def test_invalid_agent_limits_are_rejected(self):
        with self.assertRaises(ValueError):
            AgentSpec("agent_one", "skill_one", ("research",), max_concurrent_tasks=True)
        with self.assertRaises(ValueError):
            AgentOrchestrator(self.registry, max_tasks=11)


if __name__ == "__main__":
    unittest.main()
