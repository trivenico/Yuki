import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from core.auth import AuthenticationError
from core.authenticated_capability_approval_service import AuthenticatedCapabilityApprovalService
from core.capability_approval_gateway import CapabilityApprovalGateway
from core.tool_policy import Decision, PolicyRequest, PolicyRule, Risk, ToolPolicyEngine


class FakeAuth:
    def __init__(self):
        self.sessions = {
            "approver-session": SimpleNamespace(user_id="approver-1", permissions=frozenset({"workflow:approve", "workflow:read"})),
            "reader-session": SimpleNamespace(user_id="reader-1", permissions=frozenset({"workflow:read"})),
            "owner-session": SimpleNamespace(user_id="alice", permissions=frozenset({"workflow:approve", "workflow:read"})),
            "requester-session": SimpleNamespace(user_id="alice", permissions=frozenset({"workflow:read"})),
        }

    def resolve_session(self, token):
        return self.sessions.get(token)


class AuthenticatedCapabilityApprovalServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        policy = ToolPolicyEngine((
            PolicyRule(
                "approval-required", Decision.REQUIRE_APPROVAL,
                capability="workspace:write", principal_id="alice",
                target="repo:project-a", risk=Risk.HIGH,
            ),
        ), policy_version="policy-v1")
        self.gateway = CapabilityApprovalGateway(
            Path(self.temp.name) / "approval.sqlite",
            policy,
            authorize_approver=lambda actor, request: actor == "approver-1",
        )
        self.auth = FakeAuth()
        self.service = AuthenticatedCapabilityApprovalService(self.auth, self.gateway)
        self.request = PolicyRequest(
            principal_id="alice", trigger="durable_task",
            capability="workspace:write", target="repo:project-a",
            argument_sha256="a" * 64, risk=Risk.HIGH,
            run_id="goal-1", step_id="task-1",
        )

    def test_approval_uses_authenticated_identity_not_caller_supplied_actor(self):
        token = self.service.approve("approver-session", self.request)
        result = self.gateway.authorize(self.request, approval_token=token)
        self.assertTrue(result.allowed)
        self.assertTrue(result.approval_consumed)

    def test_unauthenticated_user_cannot_approve_or_revoke(self):
        with self.assertRaises(AuthenticationError):
            self.service.approve("invalid-session", self.request)
        with self.assertRaises(AuthenticationError):
            self.service.revoke("invalid-session", "x" * 40)

    def test_read_only_user_cannot_approve_or_revoke(self):
        with self.assertRaises(PermissionError):
            self.service.approve("reader-session", self.request)
        token = self.gateway.issue_approval(self.request, approved_by="approver-1")
        with self.assertRaises(PermissionError):
            self.service.revoke("reader-session", token)

    def test_audit_requires_authenticated_read_permission(self):
        with self.assertRaises(AuthenticationError):
            self.service.audit_events("invalid-session")
        self.assertEqual(self.service.audit_events("reader-session"), ())

    def test_approval_credential_is_not_returned_by_audit(self):
        token = self.service.approve("approver-session", self.request)
        audit = repr(self.service.audit_events("reader-session"))
        self.assertNotIn(token, audit)

    def test_authenticated_inbox_delivery_keeps_token_out_of_response(self):
        delivered = []
        service = AuthenticatedCapabilityApprovalService(
            self.auth, self.gateway,
            deliver_credential=lambda request_id, token: delivered.append((request_id, token)),
        )
        request_id = service.request_approval("requester-session", self.request)
        self.assertEqual(len(service.pending_requests("approver-session")), 1)
        self.assertIsNone(service.approve_pending("approver-session", request_id))
        self.assertEqual(delivered[0][0], request_id)
        decision = self.gateway.authorize(self.request, approval_token=delivered[0][1])
        self.assertTrue(decision.allowed)

    def test_inbox_rejects_cross_principal_request_creation(self):
        with self.assertRaises(PermissionError):
            self.service.request_approval("approver-session", self.request)

    def test_delivery_failure_revokes_credential(self):
        delivered = []

        def failing_delivery(request_id, token):
            delivered.append(token)
            raise RuntimeError("sensitive callback detail")

        service = AuthenticatedCapabilityApprovalService(
            self.auth, self.gateway, deliver_credential=failing_delivery,
        )
        request_id = service.request_approval("requester-session", self.request)
        with self.assertRaisesRegex(RuntimeError, "delivery failed"):
            service.approve_pending("approver-session", request_id)
        self.assertFalse(self.gateway.authorize(
            self.request, approval_token=delivered[0],
        ).allowed)

    def test_approval_requires_configured_protected_delivery(self):
        request_id = self.service.request_approval("requester-session", self.request)
        with self.assertRaisesRegex(RuntimeError, "delivery is not configured"):
            self.service.approve_pending("approver-session", request_id)


if __name__ == "__main__":
    unittest.main()
