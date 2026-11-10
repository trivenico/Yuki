import tempfile
import unittest
from pathlib import Path

from core.capability_approval_gateway import CapabilityApprovalGateway
from core.tool_policy import (
    Decision, PolicyRequest, PolicyRule, Risk, ToolPolicyEngine,
)


class CapabilityApprovalGatewayTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.now = [1000.0]
        rules = (
            PolicyRule(
                "approval-required", Decision.REQUIRE_APPROVAL,
                capability="workspace:write", principal_id="alice",
                target="repo:project-a", risk=Risk.HIGH,
            ),
            PolicyRule(
                "deny-production", Decision.DENY,
                capability="workspace:write", target="repo:production",
            ),
            PolicyRule(
                "read-allowed", Decision.ALLOW,
                capability="workspace:read", principal_id="alice",
                target="repo:project-a", risk=Risk.LOW,
            ),
        )
        self.engine = ToolPolicyEngine(rules, policy_version="policy-v7")
        self.gateway = CapabilityApprovalGateway(
            Path(self.temp.name) / "approvals.sqlite",
            self.engine,
            authorize_approver=lambda actor, request: actor == "admin",
            clock=lambda: self.now[0],
        )
        self.request = self.make_request()

    def make_request(self, *, argument_sha256="a" * 64, capability="workspace:write",
                     target="repo:project-a", run_id="run-1", step_id="step-1"):
        return PolicyRequest(
            principal_id="alice", trigger="durable_task",
            capability=capability, target=target,
            argument_sha256=argument_sha256, risk=Risk.HIGH if capability.endswith("write") else Risk.LOW,
            run_id=run_id, step_id=step_id,
        )

    def test_approval_is_bound_to_exact_request_and_consumed_once(self):
        token = self.gateway.issue_approval(self.request, approved_by="admin")
        first = self.gateway.authorize(self.request, approval_token=token)
        second = self.gateway.authorize(self.request, approval_token=token)
        self.assertTrue(first.allowed)
        self.assertTrue(first.approval_consumed)
        self.assertEqual(first.policy_version, "policy-v7")
        self.assertFalse(second.allowed)
        self.assertEqual(second.status, "approval_invalid")

    def test_approval_cannot_be_replayed_for_different_arguments(self):
        token = self.gateway.issue_approval(self.request, approved_by="admin")
        changed = self.make_request(argument_sha256="b" * 64)
        rejected = self.gateway.authorize(changed, approval_token=token)
        self.assertFalse(rejected.allowed)
        self.assertEqual(rejected.status, "approval_invalid")
        # A mismatched attempt does not burn the legitimate request's token.
        self.assertTrue(self.gateway.authorize(
            self.request, approval_token=token
        ).allowed)

    def test_expired_approval_is_rejected(self):
        token = self.gateway.issue_approval(self.request, approved_by="admin", ttl_seconds=10)
        self.now[0] += 10
        result = self.gateway.authorize(self.request, approval_token=token)
        self.assertFalse(result.allowed)
        self.assertEqual(result.status, "approval_invalid")

    def test_revocation_requires_authorized_actor_and_is_audited(self):
        token = self.gateway.issue_approval(self.request, approved_by="admin")
        with self.assertRaises(PermissionError):
            self.gateway.revoke(token, revoked_by="alice")
        self.assertTrue(self.gateway.revoke(token, revoked_by="admin"))
        result = self.gateway.authorize(self.request, approval_token=token)
        self.assertFalse(result.allowed)
        self.assertEqual(result.status, "approval_invalid")
        events = self.gateway.audit_events()
        self.assertEqual(events[0]["event_type"], "approval_revoked")
        self.assertEqual(events[0]["outcome"], "revoked")

    def test_unmatched_or_denied_policy_never_allows_execution(self):
        denied = self.make_request(target="repo:production")
        result = self.gateway.authorize(denied)
        self.assertFalse(result.allowed)
        self.assertEqual(result.status, "deny")
        unknown = self.make_request(capability="shell:execute")
        result = self.gateway.authorize(unknown)
        self.assertFalse(result.allowed)
        self.assertEqual(result.status, "deny")

    def test_deny_overrides_approval_requirement(self):
        engine = ToolPolicyEngine((
            PolicyRule("approval", Decision.REQUIRE_APPROVAL, capability="workspace:write"),
            PolicyRule("explicit-deny", Decision.DENY, principal_id="alice"),
        ), policy_version="policy-deny")
        gateway = CapabilityApprovalGateway(
            Path(self.temp.name) / "deny.sqlite", engine,
            authorize_approver=lambda *_: True, clock=lambda: self.now[0],
        )
        with self.assertRaises(PermissionError):
            gateway.issue_approval(self.request, approved_by="admin")
        self.assertFalse(gateway.authorize(self.request).allowed)

    def test_approval_issuance_requires_trusted_approver(self):
        with self.assertRaises(PermissionError):
            self.gateway.issue_approval(self.request, approved_by="alice")

    def test_approval_survives_gateway_reconstruction(self):
        token = self.gateway.issue_approval(self.request, approved_by="admin")
        reopened = CapabilityApprovalGateway(
            Path(self.temp.name) / "approvals.sqlite", self.engine,
            authorize_approver=lambda actor, request: actor == "admin",
            clock=lambda: self.now[0],
        )
        result = reopened.authorize(self.request, approval_token=token)
        self.assertTrue(result.allowed)

    def test_audit_contains_digest_not_raw_arguments_or_token(self):
        token = self.gateway.issue_approval(self.request, approved_by="admin")
        self.gateway.authorize(self.request, approval_token=token)
        audit = repr(self.gateway.audit_events())
        self.assertNotIn(token, audit)
        self.assertNotIn("secret-argument", audit)
        self.assertIn("a" * 64, audit)


if __name__ == "__main__":
    unittest.main()
