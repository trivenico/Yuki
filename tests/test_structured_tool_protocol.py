import json
import unittest

from core.guarded_tool_runtime import GuardedToolRuntime
from core.tool_action_context import ToolActionContext
from core.structured_tool_protocol import (
    StructuredToolProtocol,
    ToolProtocolError,
    parse_tool_calls,
    StructuredToolCall,
)


class Policy:
    def __init__(self, allowed=True):
        self.allowed = allowed
        self.requests = []

    def authorize_tool(self, name):
        self.requests.append(name)
        return self.allowed


class StructuredToolProtocolTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.audit = []
        self.policy = Policy()
        runtime = GuardedToolRuntime(
            {"echo": lambda argument: self.calls.append(json.loads(argument)) or argument},
            self.policy,
            audit=lambda action, outcome: self.audit.append((action, outcome)),
        )
        self.protocol = StructuredToolProtocol(runtime)

    def envelope(self, calls):
        return json.dumps({"tool_calls": calls})

    def call(self, call_id="call_1", name="echo", arguments=None):
        return {"id": call_id, "name": name, "arguments": arguments or {"text": "hello"}}

    def test_parse_and_dispatch_registered_call(self):
        results = self.protocol.dispatch(self.envelope([self.call()]))
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].call_id, "call_1")
        self.assertEqual(results[0].name, "echo")
        self.assertEqual(json.loads(results[0].output), {"text": "hello"})
        self.assertEqual(self.policy.requests, ["echo"])
        self.assertEqual(self.audit[-1], ("tool:echo", "completed"))

    def test_multiple_calls_preserve_order_and_ids(self):
        results = self.protocol.dispatch(self.envelope([
            self.call("first", arguments={"n": 1}),
            self.call("second", arguments={"n": 2}),
        ]))
        self.assertEqual([item.call_id for item in results], ["first", "second"])
        self.assertEqual(self.calls, [{"n": 1}, {"n": 2}])

    def test_malformed_later_call_prevents_partial_execution(self):
        payload = self.envelope([
            self.call("valid"),
            {"id": "bad", "name": "echo", "arguments": []},
        ])
        with self.assertRaises(ToolProtocolError):
            self.protocol.dispatch(payload)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.policy.requests, [])

    def test_duplicate_ids_and_unknown_fields_are_rejected(self):
        with self.assertRaises(ToolProtocolError):
            parse_tool_calls(self.envelope([self.call("same"), self.call("same")]))
        with self.assertRaises(ToolProtocolError):
            parse_tool_calls(json.dumps({"tool_calls": [], "extra": True}))

    def test_invalid_shapes_and_limits_are_rejected(self):
        invalid = (
            "",
            "not json",
            json.dumps({"tool_calls": "not-a-list"}),
            self.envelope([{"id": "x", "name": "echo"}]),
            self.envelope([self.call(name="../echo")]),
            self.envelope([self.call(arguments=["not", "object"])]),
        )
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ToolProtocolError):
                parse_tool_calls(value)
        with self.assertRaises(ToolProtocolError):
            parse_tool_calls(self.envelope([self.call(str(i)) for i in range(6)]))

    def test_policy_denial_does_not_execute(self):
        self.policy.allowed = False
        results = self.protocol.dispatch(self.envelope([self.call()]))
        self.assertIn("blocked", results[0].output)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.audit[-1], ("tool:echo", "denied"))

    def test_empty_batch_and_result_format_are_valid(self):
        self.assertEqual(self.protocol.dispatch(self.envelope([])), ())
        encoded = self.protocol.format_results(())
        self.assertEqual(json.loads(encoded), {"tool_results": []})

    def test_tool_outputs_are_marked_untrusted(self):
        results = self.protocol.dispatch(self.envelope([self.call()]))
        evidence = json.loads(self.protocol.format_results(results))
        self.assertEqual(evidence["tool_results"][0]["trust"], "untrusted_tool_output")

    def test_runtime_type_is_required(self):
        with self.assertRaises(TypeError):
            StructuredToolProtocol(object())


    def test_direct_dispatch_revalidates_forged_structured_calls(self):
        invalid_calls = (
            StructuredToolCall("../bad", "echo", {"text": "hello"}),
            StructuredToolCall("call_1", "../echo", {"text": "hello"}),
            StructuredToolCall("call_1", "echo", {"text": "x" * 8_001}),
            StructuredToolCall("call_1", "echo", {"value": float("nan")}),
            StructuredToolCall("call_1", "echo", {"value": object()}),
            StructuredToolCall("call_1", "echo", []),
        )
        for call in invalid_calls:
            with self.subTest(call=call), self.assertRaises(ToolProtocolError):
                self.protocol.dispatch_call(call)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.policy.requests, [])

    def test_direct_dispatch_accepts_bounded_plain_json_arguments(self):
        call = StructuredToolCall("call_direct", "echo", {"nested": [True, None, 3]})
        result = self.protocol.dispatch_call(call)
        self.assertEqual(result.call_id, "call_direct")
        self.assertEqual(json.loads(result.output), {"nested": [True, None, 3]})

    def test_direct_dispatch_rejects_excessive_structure_before_serialization(self):
        call = StructuredToolCall("call_large", "echo", {"items": [0] * 4097})
        with self.assertRaises(ToolProtocolError):
            self.protocol.dispatch_call(call)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.policy.requests, [])



    def test_trusted_context_factory_and_confirmation_reach_guarded_runtime(self):
        import tempfile
        from pathlib import Path
        from core.tool_action_journal import ToolActionJournal
        from core.tool_capabilities import ToolCapability, ToolCapabilityCatalog, ToolEffect

        with tempfile.TemporaryDirectory() as directory:
            journal = ToolActionJournal(str(Path(directory) / "actions.sqlite3"))
            from core.tool_action_approvals import SQLiteToolActionApprovalStore
            approvals = SQLiteToolActionApprovalStore(str(Path(directory) / "actions.sqlite3"))
            executions = []
            runtime = GuardedToolRuntime(
                {"send": lambda raw: executions.append(raw) or "sent"},
                self.policy,
                capabilities=ToolCapabilityCatalog({
                    "send": ToolCapability(ToolEffect.SIDE_EFFECTING, requires_user_confirmation=True),
                }),
                require_action_context=True,
                action_journal=journal,
                approval_store=approvals,
            )
            protocol = StructuredToolProtocol(runtime)
            request = {"to": "person@example.com"}
            context = ToolActionContext.create("user-1", "send", request)
            response = self.envelope([self.call("model-id-A", "send", request)])
            result = protocol.dispatch(
                response, principal_id="user-1",
                action_context_factory=lambda principal, name, args: context,
                confirmation_check=lambda call: True,
                approval_id_factory=lambda principal, action, call: approvals.issue(action, principal_id=principal),
            )
            self.assertEqual(result[0].output, "sent")
            self.assertEqual(len(executions), 1)

    def test_model_call_id_does_not_control_durable_action_identity(self):
        import tempfile
        from pathlib import Path
        from core.tool_action_journal import ToolActionJournal
        from core.tool_capabilities import ToolCapability, ToolCapabilityCatalog, ToolEffect

        with tempfile.TemporaryDirectory() as directory:
            journal = ToolActionJournal(str(Path(directory) / "actions.sqlite3"))
            executions = []
            runtime = GuardedToolRuntime(
                {"send": lambda raw: executions.append(raw) or "sent"},
                self.policy,
                capabilities=ToolCapabilityCatalog({
                    "send": ToolCapability(ToolEffect.SIDE_EFFECTING),
                }),
                require_action_context=True,
                action_journal=journal,
            )
            protocol = StructuredToolProtocol(runtime)
            request = {"to": "person@example.com"}
            context = ToolActionContext.create("user-1", "send", request)
            for model_id in ("model-id-A", "model-id-B"):
                result = protocol.dispatch(
                    self.envelope([self.call(model_id, "send", request)]),
                    principal_id="user-1",
                    action_context_factory=lambda *args: context,
                )
                self.assertEqual(result[0].output, "sent")
            self.assertEqual(len(executions), 1)


    def test_approval_token_is_not_minted_without_positive_host_confirmation(self):
        minted = []
        result = self.protocol.dispatch(
            self.envelope([self.call("approval-check", "echo")]),
            confirmation_check=lambda call: False,
            approval_id_factory=lambda *args: minted.append(True) or "token",
        )
        self.assertEqual(minted, [])
        self.assertEqual(len(result), 1)

    def test_confirmation_callback_must_return_a_real_boolean(self):
        from core.tool_capabilities import ToolCapability, ToolCapabilityCatalog, ToolEffect
        runtime = GuardedToolRuntime(
            {"echo": lambda argument: argument},
            self.policy,
            capabilities=ToolCapabilityCatalog({
                "echo": ToolCapability(ToolEffect.SIDE_EFFECTING, requires_user_confirmation=True),
            }),
        )
        protocol = StructuredToolProtocol(runtime)
        with self.assertRaises(TypeError):
            protocol.dispatch(
                self.envelope([self.call("approval-check", "echo")]),
                confirmation_check=lambda call: 1,
                approval_id_factory=lambda *args: "token",
            )


    def test_confirmation_callback_and_token_factory_are_skipped_for_non_confirming_tool(self):
        confirmation_calls = []
        approval_calls = []
        result = self.protocol.dispatch(
            self.envelope([self.call("read-only", "echo")]),
            confirmation_check=lambda call: confirmation_calls.append(call) or True,
            approval_id_factory=lambda *args: approval_calls.append(args) or "token",
        )
        self.assertEqual(len(result), 1)
        self.assertEqual(confirmation_calls, [])
        self.assertEqual(approval_calls, [])
        self.assertEqual(len(self.calls), 1)


if __name__ == "__main__":
    unittest.main()
