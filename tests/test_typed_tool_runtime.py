import unittest

from core.task_contracts import ApprovalDecision, RiskLevel, ToolCall, ToolDefinition
from core.typed_tool_runtime import TypedToolRuntime


class Policy:
    def __init__(self, allowed=True):
        self.allowed = allowed

    def authorize_tool(self, name):
        return self.allowed


def definition(name="note.save", *, side_effecting=False, risk=RiskLevel.LOW):
    return ToolDefinition(
        name, "Save a note",
        {
            "type": "object",
            "properties": {"text": {"type": "string", "minLength": 1, "maxLength": 100}},
            "required": ["text"],
            "additionalProperties": False,
        },
        risk=risk,
        side_effecting=side_effecting,
        capabilities=("workspace.write",) if side_effecting else (),
    )


class TypedToolRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.events = []
        self.tool = definition(side_effecting=True)
        self.runtime = TypedToolRuntime(
            {"note.save": (self.tool, lambda args: self.calls.append(dict(args)) or {"saved": True})},
            Policy(),
            audit=lambda action, outcome: self.events.append((action, outcome)),
        )

    def approval_for(self, call, approved=True):
        return ApprovalDecision(
            call_id=call.call_id,
            call_fingerprint=call.fingerprint,
            approved=approved,
            reason="Reviewed exact note content",
            policy_version="test-policy-v1",
        )

    def test_side_effecting_tool_requires_exact_approval(self):
        call = ToolCall("task-1", "note.save", {"text": "hello"}, call_id="call-1")
        result = self.runtime.invoke(call)
        self.assertFalse(result.ok)
        self.assertEqual(result.error_code, "approval_required")
        self.assertEqual(self.calls, [])

        approved = self.runtime.invoke(call, approval=self.approval_for(call))
        self.assertTrue(approved.ok)
        self.assertEqual(approved.output, '{"saved": true}')
        self.assertEqual(self.calls, [{"text": "hello"}])

    def test_approval_cannot_be_replayed_for_changed_arguments(self):
        original = ToolCall("task-1", "note.save", {"text": "safe"}, call_id="call-2")
        changed = ToolCall("task-1", "note.save", {"text": "different"}, call_id="call-2")
        result = self.runtime.invoke(changed, approval=self.approval_for(original))
        self.assertEqual(result.error_code, "approval_required")
        self.assertEqual(self.calls, [])

    def test_explicit_denial_never_executes(self):
        call = ToolCall("task-1", "note.save", {"text": "hello"})
        result = self.runtime.invoke(call, approval=self.approval_for(call, approved=False))
        self.assertEqual(result.error_code, "approval_required")
        self.assertEqual(self.calls, [])

    def test_invalid_schema_and_unknown_tools_fail_closed(self):
        bad = ToolCall("task-1", "note.save", {"text": "ok", "shell": True})
        self.assertEqual(self.runtime.invoke(bad).error_code, "invalid_arguments")
        missing = ToolCall("task-1", "shell.run", {"text": "x"})
        self.assertEqual(self.runtime.invoke(missing).error_code, "tool_not_found")
        self.assertEqual(self.calls, [])

    def test_policy_false_or_exception_denies(self):
        call = ToolCall("task-1", "note.save", {"text": "hello"})
        denied = TypedToolRuntime(
            {"note.save": (self.tool, lambda args: self.calls.append(args))}, Policy(False)
        )
        self.assertEqual(denied.invoke(call, approval=self.approval_for(call)).error_code, "permission_denied")

        class BrokenPolicy:
            def authorize_tool(self, name):
                raise RuntimeError("policy backend unavailable")

        broken = TypedToolRuntime(
            {"note.save": (self.tool, lambda args: self.calls.append(args))}, BrokenPolicy()
        )
        self.assertEqual(broken.invoke(call, approval=self.approval_for(call)).error_code, "permission_denied")
        self.assertEqual(self.calls, [])

    def test_high_risk_read_only_tool_also_requires_approval(self):
        high = definition("workspace.inspect", risk=RiskLevel.HIGH)
        runtime = TypedToolRuntime({"workspace.inspect": (high, lambda args: "inspected")}, Policy())
        call = ToolCall("task-1", "workspace.inspect", {"text": "README"})
        self.assertEqual(runtime.invoke(call).error_code, "approval_required")
        self.assertTrue(runtime.invoke(call, approval=self.approval_for(call)).ok)

    def test_output_is_bounded_and_non_json_results_fail_closed(self):
        tool = definition()
        runtime = TypedToolRuntime(
            {"note.save": (tool, lambda args: "x" * 30)}, Policy(), max_output_chars=10
        )
        call = ToolCall("task-1", "note.save", {"text": "ok"})
        result = runtime.invoke(call)
        self.assertTrue(result.ok)
        self.assertLessEqual(len(result.output), 10)
        self.assertTrue(result.output.endswith("… [truncated]"))
        tiny = TypedToolRuntime(
            {"note.save": (tool, lambda args: "x" * 30)}, Policy(), max_output_chars=1
        )
        self.assertEqual(len(tiny.invoke(call).output), 1)

        bad = TypedToolRuntime(
            {"note.save": (tool, lambda args: float("nan"))}, Policy()
        )
        self.assertEqual(bad.invoke(call).error_code, "tool_execution_failed")

    def test_handler_exception_does_not_leak_details(self):
        runtime = TypedToolRuntime(
            {"note.save": (self.tool, lambda args: (_ for _ in ()).throw(RuntimeError("secret")) )},
            Policy(),
        )
        call = ToolCall("task-1", "note.save", {"text": "hello"})
        result = runtime.invoke(call, approval=self.approval_for(call))
        self.assertEqual(result.error_code, "tool_execution_failed")
        self.assertNotIn("secret", result.output)

    def test_audit_failure_does_not_change_tool_result(self):
        runtime = TypedToolRuntime(
            {"note.save": (self.tool, lambda args: "saved")}, Policy(),
            audit=lambda *_: (_ for _ in ()).throw(OSError("audit unavailable")),
        )
        call = ToolCall("task-1", "note.save", {"text": "hello"})
        self.assertTrue(runtime.invoke(call, approval=self.approval_for(call)).ok)

    def test_registry_rejects_mismatched_names_and_handlers(self):
        with self.assertRaises(ValueError):
            TypedToolRuntime({"other": (self.tool, lambda _: None)}, Policy())
        with self.assertRaises(TypeError):
            TypedToolRuntime({"note.save": (self.tool, "not callable")}, Policy())


if __name__ == "__main__":
    unittest.main()
