import json
import unittest

from core.guarded_tool_runtime import GuardedToolRuntime
from core.structured_tool_protocol import (
    StructuredToolProtocol,
    ToolProtocolError,
    parse_tool_calls,
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

    def test_non_standard_json_constants_are_rejected_before_dispatch(self):
        for value in (
            '{"tool_calls":[{"id":"x","name":"echo","arguments":{"n":NaN}}]}',
            '{"tool_calls":[{"id":"x","name":"echo","arguments":{"n":Infinity}}]}',
            '{"tool_calls":[{"id":"x","name":"echo","arguments":{"n":-Infinity}}]}',
        ):
            with self.subTest(value=value), self.assertRaises(ToolProtocolError):
                self.protocol.dispatch(value)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.policy.requests, [])

    def test_duplicate_json_keys_are_rejected_at_every_object_level(self):
        values = (
            '{"tool_calls":[],"tool_calls":[]}',
            '{"tool_calls":[{"id":"x","name":"echo","arguments":{"n":1,"n":2}}]}',
        )
        for value in values:
            with self.subTest(value=value), self.assertRaises(ToolProtocolError):
                self.protocol.dispatch(value)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.policy.requests, [])

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


if __name__ == "__main__":
    unittest.main()
