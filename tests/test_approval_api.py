import asyncio
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from api.approvals import AuthenticatedCapabilityApprovalAPI
from core.auth import BuiltInAuthService
from core.authenticated_capability_approval_service import AuthenticatedCapabilityApprovalService
from core.capability_approval_gateway import CapabilityApprovalGateway
from core.capability_approval_preview import ApprovalPreview, CapabilityApprovalPreviewRegistry
from core.tool_policy import Decision, PolicyRequest, PolicyRule, Risk, ToolPolicyEngine, argument_digest
from core.approval_operation import ApprovalOperation
from automation.approved_workspace_changes import ApprovedWorkspaceChanges, FileChange
from automation.authorized_read_tools import ToolPrincipal
from core.approved_workspace_operation_executor import apply_claim_bound_workspace_changes


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
        previews = CapabilityApprovalPreviewRegistry()
        previews.register(
            "workspace:write",
            lambda request: ApprovalPreview(
                title="Write to project workspace",
                operation="Create or update project files",
                target="Project A workspace",
                impact="Project files may be changed; review the proposed change before continuing",
            ),
            operation_type="workspace.write",
        )
        # Test-only authenticated envelope; production requires a reviewed AEAD/KMS adapter.
        def seal(plaintext, aad):
            return b"test-envelope:" + hashlib.sha256(aad).digest() + plaintext
        def unseal(ciphertext, aad):
            prefix = b"test-envelope:" + hashlib.sha256(aad).digest()
            if not ciphertext.startswith(prefix):
                raise ValueError("test envelope authentication failed")
            return ciphertext[len(prefix):]
        self.gateway = CapabilityApprovalGateway(
            root / "approval.sqlite", self.policy,
            authorize_approver=lambda actor, request: actor == self.admin.user.user_id,
            preview_registry=previews,
            seal_operation_payload=seal,
            unseal_operation_payload=unseal,
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
        self.assertTrue(body["requests"][0]["preview_available"])
        self.assertEqual(
            body["requests"][0]["preview"]["operation"],
            "Create or update project files",
        )
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

    def test_authenticated_inbox_includes_exact_typed_workspace_payload(self):
        payload = {"changes": [{
            "path": "README.md",
            "expected_sha256": hashlib.sha256(b"old contents").hexdigest(),
            "content": "new approved contents",
        }]}
        operation = ApprovalOperation.from_payload(
            operation_type="workspace.write", capability="workspace:write",
            target="workspace:project-a", payload=payload,
        )
        request_data = self.payload("goal-typed-preview")
        request_data["argument_sha256"] = argument_digest(payload)
        request = PolicyRequest(**{**request_data, "risk": Risk.HIGH})
        request_id = self.gateway.request_approval(
            request, operation=operation, operation_payload=payload
        )
        status, body = self.request(
            "GET", "/v1/approvals/inbox", token=self.admin.token
        )
        self.assertEqual(status, 200)
        item = next(row for row in body["requests"] if row["request_id"] == request_id)
        self.assertTrue(item["operation_payload_available"])
        self.assertEqual(item["operation_payload"], payload)
        self.assertEqual(item["operation"]["operation_type"], "workspace.write")

    def test_claim_bound_typed_workspace_execution_applies_only_exact_approved_payload(self):
        root = Path(self.tmp.name) / "workspace"
        root.mkdir()
        target = root / "README.md"
        target.write_text("old contents", encoding="utf-8")
        change = FileChange(
            "README.md", hashlib.sha256(b"old contents").hexdigest(),
            "new approved contents",
        )
        from core.workspace_approval_operation import build_workspace_approval_operation
        operation, payload = build_workspace_approval_operation(
            workspace_id="workspace:project-a", changes=[change],
        )
        request_data = self.payload("goal-execute-exact")
        request_data["argument_sha256"] = operation.payload_sha256
        request = PolicyRequest(**{**request_data, "risk": Risk.HIGH})
        request_id = self.gateway.request_approval(
            request, operation=operation, operation_payload=payload,
        )
        self.service.approve_pending(self.admin.token, request_id)
        approval_token = self.delivered[-1][1]
        principal = ToolPrincipal(
            self.requester.user_id, frozenset({"workspace:write"}),
        )
        executor = ApprovedWorkspaceChanges(
            root, trusted_principal=lambda p: p.actor_id == self.requester.user_id,
            approve_change=lambda actor, change_id, digest: True,
            audit=lambda event: None, allowed_paths=("README.md",),
        )
        result = apply_claim_bound_workspace_changes(
            gateway=self.gateway, executor=executor, request=request,
            approval_token=approval_token, principal=principal,
            workspace_id="workspace:project-a", changes=[change],
            validate_live_claim=lambda: True,
        )
        self.assertEqual(target.read_text(encoding="utf-8"), "new approved contents")
        self.assertEqual(result.changed_paths, ("README.md",))

    def test_claim_loss_at_final_workspace_commit_fails_closed(self):
        root = Path(self.tmp.name) / "workspace-claim-loss"
        root.mkdir()
        target = root / "README.md"
        target.write_text("old contents", encoding="utf-8")
        change = FileChange(
            "README.md", hashlib.sha256(b"old contents").hexdigest(),
            "new approved contents",
        )
        from core.workspace_approval_operation import build_workspace_approval_operation
        operation, payload = build_workspace_approval_operation(
            workspace_id="workspace:project-a", changes=[change],
        )
        request_data = self.payload("goal-claim-lost")
        request_data["argument_sha256"] = operation.payload_sha256
        request = PolicyRequest(**{**request_data, "risk": Risk.HIGH})
        request_id = self.gateway.request_approval(
            request, operation=operation, operation_payload=payload,
        )
        self.service.approve_pending(self.admin.token, request_id)
        approval_token = self.delivered[-1][1]
        principal = ToolPrincipal(
            self.requester.user_id, frozenset({"workspace:write"}),
        )
        executor = ApprovedWorkspaceChanges(
            root, trusted_principal=lambda p: p.actor_id == self.requester.user_id,
            approve_change=lambda actor, change_id, digest: True,
            audit=lambda event: None, allowed_paths=("README.md",),
        )
        checks = iter((True, True, False))
        with self.assertRaises(PermissionError):
            apply_claim_bound_workspace_changes(
                gateway=self.gateway, executor=executor, request=request,
                approval_token=approval_token, principal=principal,
                workspace_id="workspace:project-a", changes=[change],
                validate_live_claim=lambda: next(checks),
            )
        self.assertEqual(target.read_text(encoding="utf-8"), "old contents")

    def test_approval_is_blocked_when_capability_has_no_trusted_preview(self):
        gateway = CapabilityApprovalGateway(
            Path(self.tmp.name) / "no-preview.sqlite",
            self.policy,
            authorize_approver=lambda actor, request: actor == self.admin.user.user_id,
        )
        request = PolicyRequest(**{**self.payload(), "risk": Risk.HIGH})
        request_id = gateway.request_approval(request)
        service = AuthenticatedCapabilityApprovalService(
            self.auth, gateway,
            deliver_credential=lambda request_id, token: self.delivered.append((request_id, token)),
        )
        with self.assertRaisesRegex(PermissionError, "trusted capability operation preview"):
            service.approve_pending(self.admin.token, request_id)
        # The alternate direct-approval facade must enforce the same preview gate.
        with self.assertRaisesRegex(PermissionError, "trusted capability operation preview"):
            service.approve(self.admin.token, request)
        self.assertEqual(self.delivered, [])
        self.assertEqual(gateway.get_request_status(request_id), "pending")

    def test_preview_snapshot_is_immutable_and_renderer_drift_blocks_approval(self):
        request = PolicyRequest(**{**self.payload("goal-preview-drift"), "risk": Risk.HIGH})
        request_id = self.gateway.request_approval(request)
        original = self.gateway.approval_preview(request_id)
        self.assertIsNotNone(original)
        self.gateway.preview_registry._renderers["workspace:write"] = (
            lambda request: ApprovalPreview(
                title="Different operation",
                operation="Delete project files",
                target="Project A workspace",
                impact="Destructive deletion",
            )
        )
        # The inbox continues to show the exact snapshot that was created with
        # this request; it must not silently change as capability code changes.
        self.assertEqual(self.gateway.approval_preview(request_id), original)
        with self.assertRaisesRegex(PermissionError, "preview changed"):
            self.gateway.approve_request(request_id, approved_by=self.admin.user.user_id)
        self.assertEqual(self.gateway.get_request_status(request_id), "pending")
        self.assertEqual(self.delivered, [])

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
