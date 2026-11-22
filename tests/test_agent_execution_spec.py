import unittest

from automation.agent_execution_spec import (
    AgentExecutionSpec,
    ExecutionBudget,
    validate_execution_graph,
)


class AgentExecutionSpecTests(unittest.TestCase):
    def make_spec(self, spec_id="planner", **overrides):
        values = {
            "spec_id": spec_id,
            "role": "planner",
            "instructions": "Plan bounded work from trusted task metadata.",
            "capabilities": ("task:read", "task:delegate"),
            "dependencies": (),
            "input_contract": {"type": "object", "required": ["request"]},
            "output_types": ("json", "markdown"),
            "approval_policy": "required",
            "retry_policy": "manual_only",
            "budget": ExecutionBudget(),
        }
        values.update(overrides)
        return AgentExecutionSpec(**values)

    def test_spec_round_trips_as_versioned_json_compatible_contract(self):
        spec = self.make_spec()
        restored = AgentExecutionSpec.from_dict(spec.to_dict())
        self.assertEqual(restored, spec)
        self.assertEqual(restored.to_dict()["version"], 1)
        self.assertEqual(restored.to_dict()["budget"]["max_steps"], 20)

    def test_rejects_unknown_fields_and_malformed_arrays(self):
        value = self.make_spec().to_dict()
        value["shell"] = True
        with self.assertRaisesRegex(ValueError, "unsupported fields"):
            AgentExecutionSpec.from_dict(value)
        value = self.make_spec().to_dict()
        value["capabilities"] = "task:read"
        with self.assertRaisesRegex(ValueError, "array of strings"):
            AgentExecutionSpec.from_dict(value)

    def test_capabilities_are_declarations_not_arbitrary_commands(self):
        with self.assertRaises(ValueError):
            self.make_spec(capabilities=("task read",))
        with self.assertRaises(ValueError):
            self.make_spec(capabilities=("Task:Read",))
        with self.assertRaises(ValueError):
            self.make_spec(capabilities=("task:read", "task:read"))

    def test_requires_bounded_instructions_and_known_contract_types(self):
        with self.assertRaises(ValueError):
            self.make_spec(instructions=" ")
        with self.assertRaises(ValueError):
            self.make_spec(instructions="x" * 4001)
        with self.assertRaises(ValueError):
            self.make_spec(output_types=("binary_executable",))
        with self.assertRaises(ValueError):
            self.make_spec(approval_policy="automatic")

    def test_budget_rejects_bool_and_out_of_range_values(self):
        with self.assertRaises(ValueError):
            ExecutionBudget(max_runtime_seconds=True)
        with self.assertRaises(ValueError):
            ExecutionBudget(max_memory_mb=8)
        with self.assertRaises(ValueError):
            ExecutionBudget(max_steps=21)

    def test_input_contract_is_deeply_immutable_and_defensively_copied(self):
        source = {"type": "object", "properties": {"request": {"type": "string"}}, "required": ["request"]}
        spec = self.make_spec(input_contract=source)
        source["properties"]["request"]["type"] = "integer"
        self.assertEqual(spec.to_dict()["input_contract"]["properties"]["request"]["type"], "string")
        with self.assertRaises(TypeError):
            spec.input_contract["type"] = "array"
        with self.assertRaises(TypeError):
            spec.input_contract["properties"]["request"]["type"] = "integer"
        with self.assertRaises(AttributeError):
            spec.input_contract["required"].append("extra")
        restored = AgentExecutionSpec.from_dict(spec.to_dict())
        self.assertEqual(restored.to_dict(), spec.to_dict())

    def test_input_contract_rejects_non_string_nested_object_keys(self):
        with self.assertRaisesRegex(ValueError, "JSON-compatible"):
            self.make_spec(input_contract={"properties": {1: "invalid"}})

    def test_dependency_graph_returns_deterministic_topological_order(self):
        specs = (
            self.make_spec("worker-b", dependencies=("worker-a",)),
            self.make_spec("worker-a"),
            self.make_spec("coordinator", dependencies=("worker-a", "worker-b")),
        )
        self.assertEqual(
            validate_execution_graph(specs),
            ("worker-a", "worker-b", "coordinator"),
        )

    def test_dependency_graph_rejects_missing_duplicate_and_cyclic_specs(self):
        with self.assertRaisesRegex(ValueError, "unknown dependencies"):
            validate_execution_graph((self.make_spec("worker-b", dependencies=("missing",)),))
        with self.assertRaisesRegex(ValueError, "unique"):
            validate_execution_graph((self.make_spec("same"), self.make_spec("same")))
        with self.assertRaisesRegex(ValueError, "cycle"):
            validate_execution_graph((
                self.make_spec("worker-a", dependencies=("worker-b",)),
                self.make_spec("worker-b", dependencies=("worker-a",)),
            ))

    def test_dependency_graph_requires_bounded_tuple(self):
        with self.assertRaises(ValueError):
            validate_execution_graph([])


if __name__ == "__main__":
    unittest.main()
