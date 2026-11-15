import unittest
import tempfile
from pathlib import Path

from automation.agent_coordinator import SpecialistAgentCoordinator, SubtaskSpec
from automation.task_runtime import TaskRuntime
from automation.workflow_store import WorkflowRunStore
from automation.specialist_registry import (
    SpecialistBudget, SpecialistDefinition, SpecialistRegistry,
)


class SpecialistRegistryTests(unittest.TestCase):
    def definition(self, **overrides):
        values = {
            "agent_id": "research-agent",
            "display_name": "Research Analyst",
            "role": "research",
            "capabilities": ("research", "summarization"),
            "action": "emit",
            "action_permissions": ("task:artifact",),
            "allowed_tools": ("read_documents",),
            "provider_preferences": ("local",),
        }
        values.update(overrides)
        return SpecialistDefinition(**values)

    def test_registry_is_immutable_and_adapts_action_permissions_separately(self):
        registry = SpecialistRegistry([self.definition()])
        self.assertEqual(registry.get("research-agent").allowed_tools, ("read_documents",))
        with self.assertRaises(TypeError):
            registry.definitions["other"] = self.definition(agent_id="other")
        agent, = registry.coordinator_agents()
        self.assertEqual(agent.permissions, frozenset({"task:artifact"}))
        self.assertNotIn("read_documents", agent.permissions)
        self.assertTrue(agent.requires_approval)

    def test_registry_validates_ids_duplicates_and_budget_ceilings(self):
        with self.assertRaises(ValueError):
            SpecialistRegistry([self.definition(), self.definition()])
        with self.assertRaises(ValueError):
            SpecialistRegistry([self.definition(allowed_tools=("read", "read"))])
        with self.assertRaises(ValueError):
            SpecialistBudget(max_tool_calls=21).validate()
        with self.assertRaises(ValueError):
            SpecialistRegistry([self.definition(agent_id="../unsafe")])

    def test_registry_caps_total_specialist_count(self):
        entries = [
            self.definition(agent_id=f"agent-{index}")
            for index in range(101)
        ]
        with self.assertRaisesRegex(ValueError, "between 1 and 100"):
            SpecialistRegistry(entries)

    def test_runtime_bindings_must_exactly_match_enabled_agents(self):
        registry = SpecialistRegistry([self.definition()])
        with self.assertRaisesRegex(ValueError, "exactly match"):
            registry.build_handlers({})
        with self.assertRaisesRegex(ValueError, "unsupported fields"):
            registry.build_handlers({
                "research-agent": {
                    "model": lambda **_: {},
                    "tools": {},
                    "tool_definitions": [],
                    "parent_policy": object(),
                    "allowed_tools": ("dangerous",),
                },
            })

    def test_registry_builds_runtime_without_accepting_binding_allowlist(self):
        class ParentPolicy:
            def authorize_tool(self, name):
                return name == "read_documents"

        seen = []
        def model(*, messages, tools, tool_choice):
            seen.append([item["function"]["name"] for item in tools])
            return {"text": "research complete", "tool_calls": {"tool_calls": []}}

        registry = SpecialistRegistry([self.definition()])
        handlers = registry.build_handlers({
            "research-agent": {
                "model": model,
                "tools": {"read_documents": lambda _: "source text"},
                "tool_definitions": [
                    {"type": "function", "function": {"name": "read_documents"}},
                ],
                "parent_policy": ParentPolicy(),
                "provider_name": "local",
            },
        })
        self.assertEqual(handlers["research-agent"]("Summarize the file"), "research complete")
        self.assertEqual(seen, [["read_documents"]])


    def test_provider_preference_mismatch_is_rejected(self):
        registry = SpecialistRegistry([self.definition()])
        with self.assertRaisesRegex(ValueError, "outside the specialist provider preferences"):
            registry.build_handlers({
                "research-agent": {
                    "model": lambda **_: {},
                    "tools": {"read_documents": lambda _: "data"},
                    "tool_definitions": [
                        {"type": "function", "function": {"name": "read_documents"}},
                    ],
                    "parent_policy": type("Policy", (), {"authorize_tool": lambda self, name: True})(),
                    "provider_name": "unapproved-cloud",
                },
            })

    def test_registry_approval_default_survives_coordinator_persistence(self):
        registry = SpecialistRegistry([self.definition()])
        with tempfile.TemporaryDirectory() as directory:
            database = str(Path(directory) / "registry.sqlite3")
            store = WorkflowRunStore(database)
            runtime = TaskRuntime(store, authorize=lambda actor, action, task: True)
            coordinator = SpecialistAgentCoordinator(
                runtime, database, registry.coordinator_agents(),
                authorize=lambda actor, action, resource: True,
            )
            plan = coordinator.submit(
                actor_id="owner", idempotency_key="registry-approval-default",
                subtasks=[SubtaskSpec(
                    "research", "Research the supplied material", ("research",),
                    requires_approval=False,
                )],
            )
            plan = coordinator.advance(actor_id="owner", plan_id=plan.plan_id)
            task = runtime.get(actor_id="owner", task_id=plan.subtasks[0].task_id)
            self.assertEqual(task.state, "queued")
            self.assertTrue(coordinator.agents["research-agent"].requires_approval)

    def test_disabled_specialists_need_no_runtime_binding(self):
        registry = SpecialistRegistry([self.definition(enabled=False)])
        self.assertEqual(registry.enabled(), ())
        self.assertEqual(registry.build_handlers({}), {})


if __name__ == "__main__":
    unittest.main()
