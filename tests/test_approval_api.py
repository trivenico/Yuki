import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from api.approvals import AuthenticatedCapabilityApprovalAPI
from core.auth import BuiltInAuthService
from core.authenticated_capability_approval_service import AuthenticatedCapabilityApprovalService
from core.capability_approval_gateway import CapabilityApprovalGateway
from core.tool_policy import Decision, PolicyRequest, PolicyRule, Risk, ToolPolicyEngine


class _Downstream:
    async def __call__(self, scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b'{"delegated":true}'})


class AuthenticatedApprovalAPITests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.auth = BuiltInAuthService(str(root / "auth.sqlite"), password_iterations=100_000)
        self.auth.bootstrap_admin("owner-admin", "a-secure-admin-password")
        self.admin = self.auth.authenticate("owner-admin", "a-secure-admin-password")
        self.requester = self.auth.create_account("task-owner", "a-secure-user-password")
        self.requester_session = self.auth.authenticate("task-owner", "a-secure-user-password")
        self.policy = ToolPolicyEngine((
            PolicyRule(
                "owner-write-approval", Decision.REQUIRE_APPROVAL,
                capability="workspace:write", principal_id=self.requester.user_id,
                target="workspace:project-a", risk=Risk.HIGH,
            ),
        ), policy_version="policy-v1")
        self.gateway = CapabilityApprovalGateway(
            root / "approval.sqlite", self.policy,
            authorize_approver=lambda actor, request: actor == self.admin.user.user_id,
        )
        self.delivered = []
        self.service = AuthenticatedCapabilityApprovalService(
            self.auth, self.gateway,
            deliver_credential=lambda request_id, token: self.delivered.append((request_id, token)),
        )
        self.app = AuthenticatedCapabilityApprovalAPI(_Downstream(), self.service)

    def request(self, method, path, payload=None, token=None):
        async def run():
            body = json.dumps(payload).encode() if payload is not None else b""
            consumed = False
            sent = []
            async def receive():
                nonlocal consumed
                if consumed:
                    return {"type": "http.disconnect"}
                consumed = True
                return {"type": "http.request", "body": body, "more_body": False}
            async def send(message):
                sent.append(message)
            headers = []
            if token:
                headers.append((b"authorization", f"Bearer {token}".encode()))
            await self.app({
                "type": "http", "method": method, "path": path, "headers": headers,
                "query_string": b"",
            }, receive, send)
            return sent[0]["status"], json.loads(sent[1]["body"])
        return asyncio.run(run())

    def payload(self, run_id="goal-001"):
        return {
            "principal_id": self.requester.user_id,
            "trigger": "durable_task",
            "capability": "workspace:write",
            "target": "workspace:project-a",
            "argument_sha256": "a" * 64,
            "risk": "high",
            "run_id": run_id,
            "step_id": "task-001",
            "claim_token_sha256": "b" * 64,
        }

    def test_request_is_owner_bound_and_approval_token_never_reaches_http(self):
        status, body = self.request(
            "POST", "/v1/approvals/requests", self.payload(),
            self.requester_session.token,
        )
        self.assertEqual(status, 201)
        request_id = body["request_id"]
        self.assertEqual(len(request_id), 32)

        status, body = self.request(
            "GET", "/v1/approvals/inbox", token=self.admin.token
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["requests"][0]["request_id"], request_id)
        self.assertEqual(body["requests"][0]["risk"], "high")
        self.assertEqual(body["requests"][0]["trigger"], "durable_task")
        self.assertNotIn("request_json", repr(body))
        self.assertNotIn("argument_sha256", repr(body))
        self.assertNotIn("claim_token_sha256", repr(body))

        status, body = self.request(
            "POST", f"/v1/approvals/requests/{request_id}/approve",
            {}, self.admin.token,
        )
        self.assertEqual((status, body), (200, {"approved": True}))
        self.assertEqual(len(self.delivered), 1)
        self.assertEqual(self.delivered[0][0], request_id)
        self.assertNotIn(self.delivered[0][1], json.dumps(body))
        request = PolicyRequest(**{
            **self.payload(), "risk": Risk.HIGH,
        })
        decision = self.gateway.authorize(request, approval_token=self.delivered[0][1])
        self.assertTrue(decision.allowed)
        self.assertTrue(decision.approval_consumed)

    def test_cross_principal_request_and_non_approver_inbox_are_rejected(self):
        payload = self.payload()
        payload["principal_id"] = "someone-else"
        status, body = self.request(
            "POST", "/v1/approvals/requests", payload, self.requester_session.token
        )
        self.assertEqual((status, body["error"]), (403, "permission_denied"))

        status, body = self.request(
            "GET", "/v1/approvals/inbox", token=self.requester_session.token
        )
        self.assertEqual((status, body["error"]), (403, "permission_denied"))

    def test_reject_route_atomically_closes_request(self):
        status, body = self.request(
            "POST", "/v1/approvals/requests", self.payload("goal-reject"),
            self.requester_session.token,
        )
        request_id = body["request_id"]
        status, body = self.request(
            "POST", f"/v1/approvals/requests/{request_id}/reject",
            {}, self.admin.token,
        )
        self.assertEqual((status, body), (200, {"rejected": True}))
        self.assertEqual(self.gateway.pending_requests(), ())

    def test_authentication_and_strict_payload_validation(self):
        status, body = self.request("GET", "/v1/approvals/inbox")
        self.assertEqual((status, body["error"]), (401, "authentication_required"))
        payload = self.payload()
        payload["approved_by"] = "attacker"
        status, body = self.request(
            "POST", "/v1/approvals/requests", payload, self.requester_session.token
        )
        self.assertEqual((status, body["error"]), (400, "invalid_request"))
        status, body = self.request(
            "POST", "/v1/approvals/requests", self.payload(), "invalid-session"
        )
        self.assertEqual((status, body["error"]), (401, "authentication_required"))

    def test_delivery_failure_returns_unavailable_without_secret(self):
        service = AuthenticatedCapabilityApprovalService(
            self.auth, self.gateway,
            deliver_credential=lambda request_id, token: (_ for _ in ()).throw(
                RuntimeError("secret=" + token)
            ),
        )
        app = AuthenticatedCapabilityApprovalAPI(_Downstream(), service)
        original = self.app
        self.app = app
        try:
            status, body = self.request(
                "POST", "/v1/approvals/requests", self.payload("goal-fail"),
                self.requester_session.token,
            )
            request_id = body["request_id"]
            status, body = self.request(
                "POST", f"/v1/approvals/requests/{request_id}/approve",
                {}, self.admin.token,
            )
            self.assertEqual((status, body), (503, {"error": "approval_delivery_unavailable"}))
            self.assertNotIn("secret=", repr(body))
        finally:
            self.app = original

    def test_unrelated_routes_are_delegated(self):
        status, body = self.request("GET", "/healthz")
        self.assertEqual((status, body["delegated"]), (200, True))


if __name__ == "__main__":
    unittest.main()
