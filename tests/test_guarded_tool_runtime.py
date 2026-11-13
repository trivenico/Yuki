"""Tests for guarded CLI tool dispatch."""
import unittest
import json

from core.guarded_tool_runtime import ApprovalRequired, GuardedToolRuntime


class Policy:
    def __init__(self, allowed=True):
        self.allowed = allowed

    def authorize_tool(self, name):
        return self.allowed


class GuardedToolRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.events = []
        self.runtime = GuardedToolRuntime(
            {"echo": lambda argument: self.calls.append(argument) or argument},
            Policy(),
            audit=lambda action, outcome: self.events.append((action, outcome)),
            max_argument_chars=10,
            max_result_chars=5,
        )

    def test_registered_tool_runs_and_audits_metadata(self):
        self.assertEqual(self.runtime.invoke("echo", "hello"), "hello")
        self.assertEqual(self.calls, ["hello"])
        self.assertEqual(self.events[-1], ("tool:echo", "completed"))

    def test_denied_tool_does_not_execute(self):
        runtime = GuardedToolRuntime(
            {"echo": lambda argument: self.calls.append(argument) or argument},
            Policy(False),
        )
        self.assertIn("blocked", runtime.invoke("echo", "hello"))
        self.assertEqual(self.calls, [])

    def test_unknown_and_oversized_arguments_fail_closed(self):
        self.assertEqual(self.runtime.invoke("missing"), "Unknown tool.")
        self.assertIn("too long", self.runtime.invoke("echo", "x" * 11))
        self.assertEqual(self.calls, [])

    def test_approved_action_requires_registry_skill_mapping_and_context_policy(self):
        calls = []
        events = []
        class ApprovedPolicy:
            def authorize_tool(self, name):
                return False
            def skill_id_for_tool(self, name):
                return "notes.write" if name == "write" else None
            def authorize_approved_tool_context(self, name, skill_id, context):
                return name == "write" and skill_id == "notes.write" and context["actor"] == "owner-a"
        runtime = GuardedToolRuntime(
            {"write": lambda raw: calls.append(json.loads(raw)) or "saved"},
            ApprovedPolicy(), audit=lambda name, outcome: events.append(outcome),
            require_audit=True,
        )
        context = {
            "actor": "owner-a", "session_id": "session-a",
            "conversation_id": "conversation-a", "request_id": "request-a",
        }
        action = {
            "tool_name": "write", "skill_id": "notes.write",
            "arguments": {"text": "approved"},
            "execution_context": context,
        }
        self.assertEqual(runtime.execute_approved_action(action, execution_context=context), "saved")
        self.assertEqual(calls, [{"text": "approved"}])
        self.assertEqual(events, ["approved_execution_started", "approved_execution_completed"])

    def test_approved_action_rejects_untrusted_skill_mapping_and_audit_failure(self):
        calls = []
        class WrongSkillPolicy:
            def authorize_tool(self, name):
                return False
            def skill_id_for_tool(self, name):
                return "notes.delete"
            def authorize_approved_tool_context(self, name, skill_id, context):
                return True
        context = {
            "actor": "owner-a", "session_id": "session-a",
            "conversation_id": "conversation-a", "request_id": "request-a",
        }
        action = {
            "tool_name": "write", "skill_id": "notes.write",
            "arguments": {"text": "approved"}, "execution_context": context,
        }
        runtime = GuardedToolRuntime(
            {"write": lambda raw: calls.append(raw)}, WrongSkillPolicy(),
            audit=lambda *_: None, require_audit=True,
        )
        with self.assertRaises(PermissionError):
            runtime.execute_approved_action(action, execution_context=context)
        self.assertEqual(calls, [])
        class ApprovedPolicy(WrongSkillPolicy):
            def skill_id_for_tool(self, name):
                return "notes.write"
        broken_audit = GuardedToolRuntime(
            {"write": lambda raw: calls.append(raw)}, ApprovedPolicy(),
            audit=lambda *_: (_ for _ in ()).throw(OSError("audit down")),
            require_audit=True,
        )
        with self.assertRaisesRegex(RuntimeError, "audit failed"):
            broken_audit.execute_approved_action(action, execution_context=context)
        self.assertEqual(calls, [])

    def test_approved_action_exception_is_not_retried_by_runtime(self):
        calls = []
        class ApprovedPolicy:
            def authorize_tool(self, name):
                return False
            def skill_id_for_tool(self, name):
                return "notes.write"
            def authorize_approved_tool_context(self, name, skill_id, context):
                return True
        context = {
            "actor": "owner-a", "session_id": "session-a",
            "conversation_id": "conversation-a", "request_id": "request-a",
        }
        action = {
            "tool_name": "write", "skill_id": "notes.write",
            "arguments": {}, "execution_context": context,
        }
        def fail(_raw):
            calls.append("called")
            raise TimeoutError("uncertain")
        runtime = GuardedToolRuntime(
            {"write": fail}, ApprovedPolicy(), audit=lambda *_: None, require_audit=True,
        )
        with self.assertRaisesRegex(RuntimeError, "outcome is unknown"):
            runtime.execute_approved_action(action, execution_context=context)
        self.assertEqual(calls, ["called"])

    def test_result_is_bounded(self):
        self.assertEqual(self.runtime.invoke("echo", "123456789"), "12345… [truncated]")

    def test_tool_exception_does_not_leak_exception(self):
        runtime = GuardedToolRuntime({"bad": lambda _: 1 / 0}, Policy())
        self.assertEqual(runtime.invoke("bad"), "The tool failed. Check the input and try again.")

    def test_policy_must_return_literal_true_to_authorize(self):
        for decision in ("false", 1, object(), None):
            with self.subTest(decision=type(decision).__name__):
                calls = []
                class NonBooleanPolicy:
                    def authorize_tool(self, name):
                        return decision
                runtime = GuardedToolRuntime(
                    {"write": lambda value: calls.append(value) or "written"},
                    NonBooleanPolicy(),
                )
                self.assertIn("blocked", runtime.invoke("write", "payload"))
                self.assertEqual(calls, [])

    def test_contextual_policy_must_return_literal_true_to_authorize(self):
        calls = []
        class NonBooleanContextPolicy:
            def authorize_tool(self, name):
                return True
            def authorize_tool_context(self, name, context):
                return "true"
        runtime = GuardedToolRuntime(
            {"write": lambda value: calls.append(value) or "written"},
            NonBooleanContextPolicy(),
        )
        context = {
            "actor": "owner-a", "session_id": "session-a",
            "conversation_id": "conversation-a", "request_id": "request-a",
        }
        self.assertIn("blocked", runtime.invoke("write", "payload", execution_context=context))
        self.assertEqual(calls, [])

    def test_approval_required_creates_pending_request_without_running_handler(self):
        calls = []
        approvals = []
        events = []

        class ApprovalPolicy:
            def authorize_tool(self, name):
                return False

            def authorize_tool_context(self, name, context):
                return ApprovalRequired({
                    "skill_id": "notes.write",
                    "arguments": {"text": "owner-approved content"},
                })

        def request_approval(name, action, context):
            approvals.append((name, action, dict(context)))
            return "pending-123"

        runtime = GuardedToolRuntime(
            {"write": lambda value: calls.append(value) or "written"},
            ApprovalPolicy(),
            audit=lambda name, outcome: events.append((name, outcome)),
            require_audit=True,
            approval_request=request_approval,
        )
        context = {
            "actor": "owner-a", "session_id": "session-a",
            "conversation_id": "conversation-a", "request_id": "request-a",
        }
        result = runtime.invoke("write", "{}", execution_context=context)
        self.assertEqual(
            result,
            '{"status":"pending_approval","pending_id":"pending-123"}',
        )
        self.assertEqual(calls, [])
        self.assertEqual(approvals[0][0], "write")
        self.assertEqual(approvals[0][1]["skill_id"], "notes.write")
        self.assertEqual(approvals[0][2], context)
        self.assertEqual(events, [("tool:write", "approval_requested")])

    def test_approval_required_fails_closed_without_host_approval_service(self):
        calls = []

        class ApprovalPolicy:
            def authorize_tool(self, name):
                return False

            def authorize_tool_context(self, name, context):
                return ApprovalRequired({"skill_id": "notes.write", "arguments": {}})

        runtime = GuardedToolRuntime(
            {"write": lambda value: calls.append(value)},
            ApprovalPolicy(),
            audit=lambda *_: None,
            require_audit=True,
        )
        context = {
            "actor": "owner-a", "session_id": "session-a",
            "conversation_id": "conversation-a", "request_id": "request-a",
        }
        self.assertIn("approval is not configured", runtime.invoke("write", "{}", execution_context=context))
        self.assertEqual(calls, [])

    def test_approval_required_does_not_create_request_if_audit_fails(self):
        calls = []
        approvals = []

        class ApprovalPolicy:
            def authorize_tool(self, name):
                return False

            def authorize_tool_context(self, name, context):
                return ApprovalRequired({"skill_id": "notes.write", "arguments": {}})

        def broken_audit(*_):
            raise OSError("audit unavailable")

        runtime = GuardedToolRuntime(
            {"write": lambda value: calls.append(value)},
            ApprovalPolicy(),
            audit=broken_audit,
            require_audit=True,
            approval_request=lambda *args: approvals.append(args) or "pending-123",
        )
        context = {
            "actor": "owner-a", "session_id": "session-a",
            "conversation_id": "conversation-a", "request_id": "request-a",
        }
        self.assertIn("audit record could not be written", runtime.invoke("write", "{}", execution_context=context))
        self.assertEqual(approvals, [])
        self.assertEqual(calls, [])

    def test_policy_exception_denies_execution(self):
        class BrokenPolicy:
            def authorize_tool(self, name):
                raise RuntimeError("policy failure")
        runtime = GuardedToolRuntime({"echo": lambda _: self.calls.append("ran")}, BrokenPolicy())
        self.assertIn("blocked", runtime.invoke("echo"))
        self.assertEqual(self.calls, [])

    def test_required_audit_runtime_rejects_missing_context_for_legacy_policy(self):
        calls = []
        runtime = GuardedToolRuntime(
            {"write": lambda value: calls.append(value) or "written"},
            Policy(),
            audit=lambda *_: None,
            require_audit=True,
        )
        result = runtime.invoke("write", "payload")
        self.assertIn("authenticated execution context is required", result)
        self.assertEqual(calls, [])
        context = {
            "actor": "owner-a", "session_id": "session-a",
            "conversation_id": "conversation-a", "request_id": "request-a",
        }
        self.assertEqual(runtime.invoke("write", "payload", execution_context=context), "written")
        self.assertEqual(calls, ["payload"])

    def test_contextual_policy_cannot_fall_back_when_context_is_missing(self):
        calls = []
        class ContextPolicy:
            def authorize_tool(self, name):
                return True
            def authorize_tool_context(self, name, context):
                return context.get("actor") == "owner-a"
        runtime = GuardedToolRuntime(
            {"write": lambda value: calls.append(value) or "written"},
            ContextPolicy(),
        )
        blocked = runtime.invoke("write", "payload")
        self.assertIn("authenticated execution context is required", blocked)
        self.assertEqual(calls, [])
        context = {
            "actor": "owner-a",
            "session_id": "session-a",
            "conversation_id": "conversation-a",
            "request_id": "request-a",
        }
        self.assertEqual(
            runtime.invoke("write", "payload", execution_context=context),
            "written",
        )
        self.assertEqual(calls, ["payload"])

    def test_rejects_non_dictionary_registry_and_non_callable_handlers(self):
        with self.assertRaises(TypeError):
            GuardedToolRuntime([], Policy())
        with self.assertRaises(TypeError):
            GuardedToolRuntime({"echo": "not callable"}, Policy())

    def test_rejects_invalid_names_and_policy_contract(self):
        for name in ("", "x" * 129, 123):
            with self.subTest(name=name), self.assertRaises((TypeError, ValueError)):
                GuardedToolRuntime({name: lambda _: "ok"}, Policy())
        with self.assertRaises(TypeError):
            GuardedToolRuntime({"echo": lambda _: "ok"}, object())

    def test_rejects_invalid_limits(self):
        for value in (0, -1, True, 1.5, "5", 1_000_001):
            with self.subTest(argument_limit=value), self.assertRaises(ValueError):
                GuardedToolRuntime({"echo": lambda _: "ok"}, Policy(), max_argument_chars=value)
            with self.subTest(result_limit=value), self.assertRaises(ValueError):
                GuardedToolRuntime({"echo": lambda _: "ok"}, Policy(), max_result_chars=value)

    def test_required_audit_rejects_missing_sink(self):
        with self.assertRaisesRegex(ValueError, "needs an audit sink"):
            GuardedToolRuntime({"echo": lambda value: value}, Policy(), require_audit=True)

    def test_required_denial_audit_failure_is_reported_without_execution(self):
        calls = []
        runtime = GuardedToolRuntime(
            {"write": lambda value: calls.append(value) or "written"},
            Policy(False),
            audit=lambda *_: (_ for _ in ()).throw(OSError("audit unavailable")),
            require_audit=True,
        )
        self.assertIn("denial audit record could not be written", runtime.invoke("write", "sensitive", execution_context={"actor": "owner:test", "session_id": "session:test", "conversation_id": "conversation:test", "request_id": "request:test"}))
        self.assertEqual(calls, [])

    def test_required_authorization_audit_failure_prevents_execution(self):
        calls = []
        runtime = GuardedToolRuntime(
            {"write": lambda value: calls.append(value) or "written"},
            Policy(),
            audit=lambda *_: (_ for _ in ()).throw(OSError("audit unavailable")),
            require_audit=True,
        )
        result = runtime.invoke("write", "sensitive", execution_context={"actor": "owner:test", "session_id": "session:test", "conversation_id": "conversation:test", "request_id": "request:test"})
        self.assertIn("blocked", result)
        self.assertEqual(calls, [])

    def test_required_completion_audit_failure_reports_recovery_without_retry_signal(self):
        calls = []
        events = []

        def audit(action, outcome):
            events.append((action, outcome))
            if outcome == "completed":
                raise OSError("audit unavailable")

        runtime = GuardedToolRuntime(
            {"write": lambda value: calls.append(value) or "written"},
            Policy(), audit=audit, require_audit=True,
        )
        result = runtime.invoke("write", "sensitive")
        self.assertIn("inspect activity before retrying", result)
        self.assertEqual(calls, ["sensitive"])
        self.assertEqual(events, [("tool:write", "authorized"), ("tool:write", "completed")])

    def test_audit_failure_does_not_change_authorization_or_result(self):
        runtime = GuardedToolRuntime(
            {"echo": lambda argument: argument},
            Policy(),
            audit=lambda *_: (_ for _ in ()).throw(OSError("audit unavailable")),
        )
        self.assertEqual(runtime.invoke("echo", "ok"), "ok")


if __name__ == "__main__":
    unittest.main()
