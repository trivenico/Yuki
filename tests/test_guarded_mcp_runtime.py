import unittest

from core.guarded_mcp_runtime import GuardedMCPRuntime
from core.tool_policy import (
    Decision, PolicyRequest, PolicyRule, Risk, ToolPolicyEngine, argument_digest,
)


class FakeClient:
    def __init__(self):
        self.calls = []

    def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        return {"ok": True}


class FakeGateway:
    def __init__(self, allowed):
        self.allowed = allowed
        self.calls = []

    def authorize(self, request, *, approval_token=None):
        self.calls.append((request, approval_token))
        return type("Approval", (), {
            "allowed": self.allowed,
            "status": "allowed" if self.allowed else "denied",
        })()


class GuardedMCPRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.client = FakeClient()
        self.args = {"query": "status"}
        self.request = PolicyRequest(
            principal_id="user-1", trigger="mcp",
            capability="mcp.tool.invoke", target="status",
            argument_sha256=argument_digest(self.args), risk=Risk.MEDIUM,
            run_id="run-1", step_id="step-1",
        )

    def factory(self, name, args):
        return self.request

    def engine(self, decision):
        return ToolPolicyEngine(
            [PolicyRule(
                rule_id="mcp-status", decision=decision,
                principal_id="user-1", trigger="mcp",
                capability="mcp.tool.invoke", target="status",
                risk=Risk.MEDIUM,
            )],
            policy_version="test-mcp-v1",
        )

    def test_allow_calls_client(self):
        runtime = GuardedMCPRuntime(self.client, self.engine(Decision.ALLOW), self.factory)
        self.assertEqual(runtime.call_tool("status", self.args), {"ok": True})
        self.assertEqual(len(self.client.calls), 1)

    def test_default_deny_blocks_network_call(self):
        runtime = GuardedMCPRuntime(
            self.client, ToolPolicyEngine([], policy_version="deny-all"), self.factory,
        )
        with self.assertRaises(PermissionError):
            runtime.call_tool("status", self.args)
        self.assertEqual(self.client.calls, [])

    def test_deny_cannot_be_overridden_by_approval_gateway(self):
        gateway = FakeGateway(True)
        runtime = GuardedMCPRuntime(
            self.client, self.engine(Decision.DENY), self.factory,
            approval_gateway=gateway,
        )
        with self.assertRaises(PermissionError):
            runtime.call_tool("status", self.args, approval_token="opaque-token")
        self.assertEqual(gateway.calls, [])
        self.assertEqual(self.client.calls, [])

    def test_approval_required_uses_gateway_before_client(self):
        gateway = FakeGateway(True)
        runtime = GuardedMCPRuntime(
            self.client, self.engine(Decision.REQUIRE_APPROVAL), self.factory,
            approval_gateway=gateway,
        )
        runtime.call_tool("status", self.args, approval_token="opaque-token")
        self.assertEqual(gateway.calls[0][1], "opaque-token")
        self.assertEqual(len(self.client.calls), 1)

    def test_missing_or_denied_approval_blocks_client(self):
        gateway = FakeGateway(False)
        runtime = GuardedMCPRuntime(
            self.client, self.engine(Decision.REQUIRE_APPROVAL), self.factory,
            approval_gateway=gateway,
        )
        with self.assertRaises(PermissionError):
            runtime.call_tool("status", self.args)
        self.assertEqual(self.client.calls, [])

    def test_request_must_bind_tool_and_exact_arguments(self):
        bad_request = PolicyRequest(
            principal_id="user-1", trigger="mcp",
            capability="mcp.tool.invoke", target="other",
            argument_sha256=argument_digest(self.args), risk=Risk.MEDIUM,
            run_id="run-1", step_id="step-1",
        )
        runtime = GuardedMCPRuntime(
            self.client, self.engine(Decision.ALLOW), lambda name, args: bad_request,
        )
        with self.assertRaises(ValueError):
            runtime.call_tool("status", self.args)
        with self.assertRaises(ValueError):
            GuardedMCPRuntime(
                self.client, self.engine(Decision.ALLOW), self.factory,
            ).call_tool("status", {"query": "changed"})
        self.assertEqual(self.client.calls, [])

    def test_audit_failure_does_not_change_execution_decision(self):
        runtime = GuardedMCPRuntime(
            self.client, self.engine(Decision.ALLOW), self.factory,
            audit=lambda action, outcome: (_ for _ in ()).throw(RuntimeError("offline")),
        )
        runtime.call_tool("status", self.args)
        self.assertEqual(len(self.client.calls), 1)


if __name__ == "__main__":
    unittest.main()
