"""Tests for constructing specialist protocols with parent-bounded tools."""
import unittest

from core.delegated_agent_runtime import build_delegated_tool_protocol


class MutableParentPolicy:
    def __init__(self, allowed):
        self.allowed = set(allowed)
        self.calls = []

    def authorize_tool(self, name):
        self.calls.append(name)
        return name in self.allowed


def envelope(name, arguments=None):
    import json
    return json.dumps({"tool_calls": [{
        "id": "call-1", "name": name, "arguments": arguments or {},
    }]})


class DelegatedAgentRuntimeTests(unittest.TestCase):
    def test_builder_exposes_only_allowlisted_handlers(self):
        parent = MutableParentPolicy({"read", "delete"})
        calls = []
        protocol = build_delegated_tool_protocol(
            tools={
                "read": lambda _: calls.append("read") or "read-result",
                "delete": lambda _: calls.append("delete") or "deleted",
            },
            parent_policy=parent,
            allowed_tools={"read"},
        )
        result = protocol.dispatch(envelope("read"))
        self.assertEqual(result[0].output, "read-result")
        denied = protocol.dispatch(envelope("delete"))
        self.assertIn("Unknown tool", denied[0].output)
        self.assertEqual(calls, ["read"])
        self.assertEqual(parent.calls, ["read"])

    def test_parent_revocation_is_checked_on_each_dispatch(self):
        parent = MutableParentPolicy({"read"})
        protocol = build_delegated_tool_protocol(
            tools={"read": lambda _: "sensitive"},
            parent_policy=parent,
            allowed_tools={"read"},
        )
        self.assertEqual(protocol.dispatch(envelope("read"))[0].output, "sensitive")
        parent.allowed.clear()
        self.assertIn("blocked", protocol.dispatch(envelope("read"))[0].output)
        self.assertEqual(parent.calls, ["read", "read"])

    def test_builder_rejects_allowlisted_tool_without_handler(self):
        with self.assertRaisesRegex(ValueError, "registered handler"):
            build_delegated_tool_protocol(
                tools={"read": lambda _: "ok"},
                parent_policy=MutableParentPolicy({"read"}),
                allowed_tools={"read", "write"},
            )

    def test_empty_allowlist_constructs_no_tool_surface(self):
        parent = MutableParentPolicy({"read"})
        protocol = build_delegated_tool_protocol(
            tools={"read": lambda _: "ok"},
            parent_policy=parent,
            allowed_tools=(),
        )
        result = protocol.dispatch(envelope("read"))
        self.assertIn("Unknown tool", result[0].output)
        self.assertEqual(parent.calls, [])


if __name__ == "__main__":
    unittest.main()
