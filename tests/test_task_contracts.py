import unittest

from core.task_contracts import (
    ApprovalDecision,
    RiskLevel,
    TaskEvent,
    TaskStatus,
    ToolCall,
    ToolDefinition,
    ToolResult,
    VerificationResult,
    validate_tool_call,
)


class TaskContractTests(unittest.TestCase):
    def test_tool_definition_requires_object_schema_and_valid_name(self):
        definition = ToolDefinition(
            "file.read",
            "Read a file from an explicitly authorized workspace",
            {"type": "object", "properties": {"path": {"type": "string"}}},
            RiskLevel.LOW,
            capabilities=("filesystem.read",),
        )
        self.assertEqual(definition.input_schema["type"], "object")
        with self.assertRaises(TypeError):
            definition.input_schema["type"] = "string"
        with self.assertRaises(ValueError):
            ToolDefinition("Shell", "Not valid", {"type": "object"})
        with self.assertRaises(ValueError):
            ToolDefinition("file.read", "Bad schema", {"type": "array"})

    def test_tool_call_arguments_are_json_bounded_and_immutable(self):
        args = {"path": "README.md", "options": {"lines": [1, 2]}}
        call = ToolCall(task_id="task-1", tool_name="file.read", arguments=args)
        original_fingerprint = call.fingerprint
        args["path"] = "outside.txt"
        self.assertEqual(call.arguments["path"], "README.md")
        with self.assertRaises(TypeError):
            call.arguments["path"] = "changed.txt"
        with self.assertRaises(TypeError):
            call.arguments["options"]["lines"][0] = 99
        self.assertEqual(call.fingerprint, original_fingerprint)

    def test_tool_call_rejects_non_json_and_oversized_arguments(self):
        with self.assertRaises(ValueError):
            ToolCall("task-1", "file.read", {"bad": object()})
        with self.assertRaises(ValueError):
            ToolCall("task-1", "file.read", {"payload": "x" * 17_000})
        with self.assertRaises(ValueError):
            ToolCall("task-1", "file.read", {"not-finite": float("nan")})

    def test_tool_call_rejects_non_string_json_object_keys(self):
        with self.assertRaisesRegex(ValueError, "keys must be strings"):
            ToolCall("task-1", "file.read", {1: "README.md"})
        with self.assertRaisesRegex(ValueError, "keys must be strings"):
            ToolCall("task-1", "file.read", {"options": {True: "ambiguous"}})

    def test_task_event_rejects_non_string_nested_object_keys(self):
        with self.assertRaisesRegex(ValueError, "keys must be strings"):
            TaskEvent("task-1", "task.progress", {"details": {1: "ambiguous"}})

    def test_registered_tool_schema_accepts_valid_call(self):
        definition = ToolDefinition(
            "file.read",
            "Read one authorized workspace file",
            {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "minLength": 1, "maxLength": 200},
                    "max_lines": {"type": "integer", "minimum": 1, "maximum": 500},
                },
                "required": ["path"],
                "additionalProperties": False,
            },
        )
        call = ToolCall("task-1", "file.read", {"path": "README.md", "max_lines": 50})
        self.assertIsNone(validate_tool_call(call, definition))

    def test_registered_tool_schema_rejects_unknown_or_invalid_arguments(self):
        definition = ToolDefinition(
            "file.read",
            "Read one authorized workspace file",
            {
                "type": "object",
                "properties": {"path": {"type": "string", "minLength": 1}},
                "required": ["path"],
                "additionalProperties": False,
            },
        )
        invalid_calls = (
            ToolCall("task-1", "file.read", {"path": ""}),
            ToolCall("task-1", "file.read", {"path": "README.md", "execute": True}),
            ToolCall("task-1", "file.read", {}),
        )
        for call in invalid_calls:
            with self.subTest(arguments=dict(call.arguments)):
                with self.assertRaises(ValueError):
                    validate_tool_call(call, definition)

    def test_registered_tool_schema_rejects_mismatched_tool_and_unsupported_keywords(self):
        definition = ToolDefinition(
            "file.read",
            "Read one authorized workspace file",
            {"type": "object", "properties": {"path": {"type": "string"}}},
        )
        with self.assertRaises(ValueError):
            validate_tool_call(ToolCall("task-1", "shell.run", {"path": "x"}), definition)
        with self.assertRaises(ValueError):
            ToolDefinition(
                "file.read",
                "Read one authorized workspace file",
                {"type": "object", "properties": {"path": {"type": "string", "pattern": ".*"}}},
            )
        with self.assertRaises(ValueError):
            ToolDefinition(
                "file.read",
                "Read one authorized workspace file",
                {"type": "object", "properties": {"path": {"type": "string", "maxLength": "many"}}},
            )

    def test_approval_is_bound_to_exact_call_not_only_call_id(self):
        call = ToolCall("task-1", "file.read", {"path": "safe.txt"}, call_id="call-1")
        approval = ApprovalDecision(
            call_id=call.call_id,
            call_fingerprint=call.fingerprint,
            approved=True,
            reason="User confirmed the exact file read",
            policy_version="policy-v1",
        )
        self.assertTrue(approval.matches(call))
        changed = ToolCall("task-1", "file.read", {"path": "other.txt"}, call_id="call-1")
        self.assertFalse(approval.matches(changed))

    def test_approval_requires_valid_fingerprint_and_explicit_boolean(self):
        with self.assertRaises(ValueError):
            ApprovalDecision("call-1", "not-a-fingerprint", True, "approved", "policy-v1")
        with self.assertRaises(ValueError):
            ApprovalDecision("call-1", "a" * 64, 1, "approved", "policy-v1")

    def test_failed_tool_result_requires_error_code(self):
        self.assertEqual(ToolResult("call-1", True, output="ok").output, "ok")
        with self.assertRaises(ValueError):
            ToolResult("call-1", False, output="failed")
        with self.assertRaises(ValueError):
            ToolResult("call-1", True, error_code="unexpected")

    def test_verified_completion_requires_evidence(self):
        verified = VerificationResult(True, "File exists", "Observed path in workspace", "filesystem.check")
        self.assertTrue(verified.verified)
        with self.assertRaises(ValueError):
            VerificationResult(True, "Done", "", "model")
        self.assertFalse(VerificationResult(False, "Not found", "Path absent", "filesystem.check").verified)

    def test_task_event_has_bounded_json_payload_and_schema_version(self):
        event = TaskEvent("task-1", "tool.completed", {"call_id": "call-1", "ok": True})
        self.assertEqual(event.schema_version, 1)
        with self.assertRaises(TypeError):
            event.payload["ok"] = False
        with self.assertRaises(ValueError):
            TaskEvent("task-1", "tool.completed", {"value": object()})
        with self.assertRaises(ValueError):
            TaskEvent("task-1", "tool.completed", {}, schema_version=2)

    def test_nested_task_event_payload_is_immutable(self):
        source = {"details": {"steps": [1, 2]}}
        event = TaskEvent("task-1", "task.progress", source)
        source["details"]["steps"].append(3)
        self.assertEqual(list(event.payload["details"]["steps"]), [1, 2])
        with self.assertRaises(TypeError):
            event.payload["details"]["new"] = "blocked"
        with self.assertRaises(AttributeError):
            event.payload["details"]["steps"].append(4)

    def test_fingerprint_is_canonical_across_mapping_order(self):
        first = ToolCall("task-1", "file.read", {"path": "README.md", "options": {"a": 1, "b": 2}})
        second = ToolCall("task-1", "file.read", {"options": {"b": 2, "a": 1}, "path": "README.md"})
        self.assertEqual(first.fingerprint, second.fingerprint)

    def test_task_statuses_are_stable_wire_values(self):
        self.assertEqual(TaskStatus.AWAITING_APPROVAL.value, "awaiting_approval")
        self.assertEqual(TaskStatus.COMPLETED.value, "completed")


if __name__ == "__main__":
    unittest.main()
