import unittest

from automation.agent_contracts import (
    AgentContract, validate_agent_input, validate_agent_output, validate_contract,
)


class AgentContractTests(unittest.TestCase):
    def setUp(self):
        self.contract = AgentContract(
            version="1.0.0",
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "minLength": 1, "maxLength": 200},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 20},
                },
                "required": ["query"],
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "properties": {"summary": {"type": "string", "minLength": 1}},
                "required": ["summary"],
                "additionalProperties": False,
            },
        )

    def test_valid_input_and_output(self):
        self.assertEqual(validate_agent_input(self.contract, {"query": "Yuki", "limit": 5}), ())
        self.assertEqual(validate_agent_output(self.contract, {"summary": "Ready"}), ())

    def test_invalid_values_return_paths_and_codes_without_echoing_values(self):
        issues = validate_agent_input(self.contract, {"query": "", "limit": 100, "secret": "never echo"})
        self.assertEqual({item.code for item in issues}, {"min_length", "maximum", "additional_property"})
        self.assertNotIn("never echo", repr(issues))

    def test_type_checks_do_not_treat_bool_as_integer(self):
        issues = validate_agent_input(self.contract, {"query": "hello", "limit": True})
        self.assertEqual(issues[0].code, "type_mismatch")

    def test_rejects_unsupported_or_malformed_schema(self):
        with self.assertRaises(ValueError):
            validate_contract({"type": "object", "$ref": "https://attacker.invalid/schema"}, {})
        with self.assertRaises(ValueError):
            validate_contract({"type": "object", "required": ["missing"], "properties": {}}, {})

    def test_rejects_non_json_oversized_and_deep_payloads(self):
        with self.assertRaises(ValueError):
            validate_contract({"type": "number"}, float("nan"))
        with self.assertRaises(ValueError):
            validate_contract({"type": "string"}, "x" * 70000)
        value = "leaf"
        for _ in range(20):
            value = [value]
        with self.assertRaises(ValueError):
            validate_contract({"type": "array", "items": {"type": "array"}}, value)


if __name__ == "__main__":
    unittest.main()
