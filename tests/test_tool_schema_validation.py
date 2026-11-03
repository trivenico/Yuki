import unittest

from core.tool_schema_validation import (
    ToolSchemaError,
    validate_tool_calls,
    validate_tool_definitions,
)
from core.structured_tool_protocol import parse_tool_calls


class ToolSchemaValidationTests(unittest.TestCase):
    def test_validates_nested_object_array_and_numeric_constraints(self):
        schemas = validate_tool_definitions([{
            "type": "function",
            "function": {
                "name": "create_report",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string", "minLength": 1, "maxLength": 20},
                        "pages": {
                            "type": "array", "minItems": 1, "maxItems": 2,
                            "items": {
                                "type": "object",
                                "properties": {"count": {"type": "integer", "minimum": 1, "maximum": 10}},
                                "required": ["count"], "additionalProperties": False,
                            },
                        },
                    },
                    "required": ["title", "pages"],
                    "additionalProperties": False,
                },
            },
        }])
        calls = parse_tool_calls(
            '{"tool_calls":[{"id":"c1","name":"create_report","arguments":{"title":"Summary","pages":[{"count":3}]}}]}'
        )
        validate_tool_calls(calls, schemas)

    def test_rejects_missing_required_and_extra_properties(self):
        schema = validate_tool_definitions([{
            "type": "function",
            "function": {"name": "echo", "parameters": {
                "type": "object", "properties": {"text": {"type": "string"}},
                "required": ["text"], "additionalProperties": False,
            }},
        }])
        for arguments in ({}, {"text": "ok", "unexpected": 1}):
            calls = parse_tool_calls(
                __import__("json").dumps({"tool_calls": [
                    {"id": "c1", "name": "echo", "arguments": arguments}
                ]})
            )
            with self.subTest(arguments=arguments), self.assertRaises(ToolSchemaError):
                validate_tool_calls(calls, schema)

    def test_rejects_wrong_types_and_bounds(self):
        schema = validate_tool_definitions([{
            "type": "function",
            "function": {"name": "set_level", "parameters": {
                "type": "object",
                "properties": {"level": {"type": "integer", "minimum": 1, "maximum": 5}},
                "required": ["level"], "additionalProperties": False,
            }},
        }])
        for value in (True, 0, 6, "3"):
            calls = parse_tool_calls(
                __import__("json").dumps({"tool_calls": [
                    {"id": "c1", "name": "set_level", "arguments": {"level": value}}
                ]})
            )
            with self.subTest(value=value), self.assertRaises(ToolSchemaError):
                validate_tool_calls(calls, schema)

    def test_rejects_duplicate_names_unknown_schema_keywords_and_bad_required(self):
        duplicate = [
            {"type": "function", "function": {"name": "echo", "parameters": {"type": "object"}}},
            {"type": "function", "function": {"name": "echo", "parameters": {"type": "object"}}},
        ]
        with self.assertRaises(ToolSchemaError):
            validate_tool_definitions(duplicate)
        for parameters in (
            {"type": "object", "properties": {}, "required": ["absent"]},
            {"type": "object", "properties": {}, "patternProperties": {}},
            {"type": "string"},
        ):
            with self.subTest(parameters=parameters), self.assertRaises(ToolSchemaError):
                validate_tool_definitions([{
                    "type": "function", "function": {"name": "bad", "parameters": parameters}
                }])

    def test_unknown_tool_is_rejected_even_if_protocol_can_parse_it(self):
        calls = parse_tool_calls(
            '{"tool_calls":[{"id":"c1","name":"not_advertised","arguments":{}}]}'
        )
        with self.assertRaises(ToolSchemaError):
            validate_tool_calls(calls, {})


if __name__ == "__main__":
    unittest.main()
