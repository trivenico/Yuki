import unittest

from core.guarded_mcp_runtime import GuardedMCPRuntime
from core.tool_policy import (
    Decision,
    PolicyRequest,
    PolicyRule,
    Risk,
    ToolPolicyEngine,
    argument_digest,
)


class FakeMCPClient:
    def __init__(self):
        self.calls = []

    def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        return {"result": "ok", "untrusted": True}


class FakeGateway:
    def __init__(self, allowed):
        self.allowed = allowed
        self.calls = []

    def authorize(self, request, *, approval_token=None):
        self.calls.append((request, approval_token))
        return type("Decision", (), {
            "allowed": self.allowed,
            "status": "allowed" if self.allowed else "approval_required",
        })()


class GuardedMCPRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.client = FakeMCPClient()
        self.arguments = {"query": "status"}
        self.request = PolicyRequest(
            principal_id="user-1",
            trigger="mcp",
            capability="mcp.tool.invoke",
            target="status",
            argument_sha256=argument_digest(self.arguments),
            risk=Risk.MEDIUM,
            run_id="run-1",
            step_id="step-1",
        )

    def factory(self, name, arguments):
        return self.request

    def engine(self, decision):
        return ToolPolicyEngine(
            [PolicyRule(
                rule_id="mcp-status",
                decision=decision,
                principal_id="user-1",
                trigger="mcp",
                capability="mcp.tool.invoke",
                target="status",
                risk=Risk.MEDIUM,
            )],
            policy_version="test-mcp-v1",
        )

    def test_allow_calls_client_only_after_policy(self):
        runtime = GuardedMCPRuntime(
            self.client, self.engine(Decision.ALLOW), self.factory,
        )
        result = runtime.call_tool("status", self.arguments)
        self.assertEqual(result["result"], "ok")
        self.assertEqual(self.client.calls, [("status", self.arguments)])

    def test_default_deny_prevents_network_call(self):
        runtime = GuardedMCPRuntime(
            self.client, ToolPolicyEngine([], policy_version="deny-all"),
            self.factory,
        )
        with self.assertRaises(PermissionError):
            runtime.call_tool("status", self.arguments)
        self.assertEqual(self.client.calls, [])

    def test_non_allow_policy_prevents_network_call(self):
        runtime = GuardedMCPRuntime(
            self.client, self.engine(Decision.REQUIRE_APPROVAL), self.factory,
        )
        with self.assertRaises(PermissionError):
            runtime.call_tool("status", self.arguments)
        self.assertEqual(self.client.calls, [])

    def test_request_must_bind_exact_tool_and_arguments(self):
        wrong_target = PolicyRequest(
            principal_id="user-1", trigger="mcp",
            capability="mcp.tool.invoke", target="other",
            argument_sha256=argument_digest(self.arguments),
            risk=Risk.MEDIUM, run_id="run-1", step_id="step-1",
        )
        runtime = GuardedMCPRuntime(
            self.client, self.engine(Decision.ALLOW),
            lambda name, arguments: wrong_target,
        )
        with self.assertRaises(ValueError):
            runtime.call_tool("status", self.arguments)
        self.assertEqual(self.client.calls, [])

        changed_arguments = {"query": "different"}
        runtime = GuardedMCPRuntime(
            self.client, self.engine(Decision.ALLOW), self.factory,
        )
        with self.assertRaises(ValueError):
            runtime.call_tool("status", changed_arguments)
        self.assertEqual(self.client.calls, [])

    def test_gateway_denial_prevents_network_call(self):
        gateway = FakeGateway(False)
        runtime = GuardedMCPRuntime(
            self.client, self.engine(Decision.REQUIRE_APPROVAL), self.factory,
            approval_gateway=gateway,
        )
        with self.assertRaises(PermissionError):
            runtime.call_tool("status", self.arguments, approval_token="bad-token")
        self.assertEqual(len(gateway.calls), 1)
        self.assertEqual(self.client.calls, [])

    def test_gateway_authorization_preserves_client_call_boundary(self):
        gateway = FakeGateway(True)
        runtime = GuardedMCPRuntime(
            self.client, self.engine(Decision.REQUIRE_APPROVAL), self.factory,
            approval_gateway=gateway,
        )
        runtime.call_tool("status", self.arguments, approval_token="opaque-token")
        self.assertEqual(gateway.calls[0][1], "opaque-token")
        self.assertEqual(len(self.client.calls), 1)

    def test_audit_failure_does_not_change_policy_result(self):
        runtime = GuardedMCPRuntime(
            self.client, self.engine(Decision.ALLOW), self.factory,
            audit=lambda action, outcome: (_ for _ in ()).throw(RuntimeError("offline")),
        )
        runtime.call_tool("status", self.arguments)
        self.assertEqual(len(self.client.calls), 1)


if __name__ == "__main__":
    unittest.main()
