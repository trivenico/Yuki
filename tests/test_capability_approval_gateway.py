import hashlib
import tempfile
from contextlib import closing
import unittest
from pathlib import Path

from core.capability_approval_gateway import CapabilityApprovalGateway
from core.approval_operation import ApprovalOperation
from core.capability_approval_preview import ApprovalPreview, CapabilityApprovalPreviewRegistry
from core.tool_policy import (
    Decision, PolicyRequest, PolicyRule, Risk, ToolPolicyEngine, argument_digest,
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
        self.previews = CapabilityApprovalPreviewRegistry()
        # Test-only reversible envelope; production must inject a reviewed AEAD/KMS adapter.
        def seal(plaintext, aad):
            return b"test-envelope:" + hashlib.sha256(aad).digest() + plaintext
        def unseal(ciphertext, aad):
            prefix = b"test-envelope:" + hashlib.sha256(aad).digest()
            if not ciphertext.startswith(prefix):
                raise ValueError("test envelope authentication failed")
            return ciphertext[len(prefix):]
        self.seal_payload = seal
        self.unseal_payload = unseal
        self.previews.register(
            "workspace:write",
            lambda request: ApprovalPreview(
                title="Workspace change", operation="Apply the exact approved change",
                target=request.target, impact=f"Request digest {request.argument_sha256[:12]}",
            ),
            operation_type="workspace.write",
        )
        self.gateway = CapabilityApprovalGateway(
            Path(self.temp.name) / "approvals.sqlite",
            self.engine,
            authorize_approver=lambda actor, request: actor == "admin",
            clock=lambda: self.now[0], preview_registry=self.previews,
            seal_operation_payload=self.seal_payload,
            unseal_operation_payload=self.unseal_payload,
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
        revocations = [event for event in events if event["event_type"] == "approval_revoked"]
        self.assertEqual(len(revocations), 1)
        self.assertEqual(revocations[0]["outcome"], "revoked")

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

    def test_policy_version_change_invalidates_pending_approval(self):
        token = self.gateway.issue_approval(self.request, approved_by="admin")
        self.gateway.policy_engine = ToolPolicyEngine((
            PolicyRule(
                "approval-required", Decision.REQUIRE_APPROVAL,
                capability="workspace:write", principal_id="alice",
                target="repo:project-a", risk=Risk.HIGH,
            ),
        ), policy_version="policy-v8")
        result = self.gateway.authorize(self.request, approval_token=token)
        self.assertFalse(result.allowed)
        self.assertEqual(result.status, "approval_invalid")

    def test_allow_decisions_are_audited(self):
        allowed = self.make_request(capability="workspace:read")
        result = self.gateway.authorize(allowed)
        self.assertTrue(result.allowed)
        self.assertEqual(self.gateway.audit_events()[0]["outcome"], "allowed")

    def test_approval_issuance_requires_trusted_approver(self):
        with self.assertRaises(PermissionError):
            self.gateway.issue_approval(self.request, approved_by="alice")

    def test_approval_survives_gateway_reconstruction(self):
        token = self.gateway.issue_approval(self.request, approved_by="admin")
        reopened = CapabilityApprovalGateway(
            Path(self.temp.name) / "approvals.sqlite", self.engine,
            authorize_approver=lambda actor, request: actor == "admin",
            clock=lambda: self.now[0], preview_registry=self.previews,
        )
        result = reopened.authorize(self.request, approval_token=token)
        self.assertTrue(result.allowed)

    def test_audit_contains_request_digest_not_raw_arguments_or_token(self):
        token = self.gateway.issue_approval(self.request, approved_by="admin")
        self.gateway.authorize(self.request, approval_token=token)
        audit = repr(self.gateway.audit_events())
        self.assertNotIn(token, audit)
        self.assertNotIn("secret-argument", audit)
        self.assertNotIn(self.request.argument_sha256, audit)
        self.assertIn(self.gateway._request_digest(self.request), audit)


    def test_durable_inbox_is_idempotent_and_redacted(self):
        request_id = self.gateway.request_approval(self.request)
        self.assertEqual(request_id, self.gateway.request_approval(self.request))
        rows = self.gateway.pending_requests()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["request_id"], request_id)
        self.assertNotIn("request_json", rows[0])
        self.assertNotIn("argument_sha256", rows[0])
        self.assertNotIn("token", repr(rows))

    def test_inbox_approval_is_atomic_one_shot_and_persistent(self):
        request_id = self.gateway.request_approval(self.request)
        token = self.gateway.approve_request(request_id, approved_by="admin")
        with self.assertRaises(PermissionError):
            self.gateway.approve_request(request_id, approved_by="admin")
        self.assertTrue(self.gateway.authorize(self.request, approval_token=token).allowed)
        self.assertFalse(self.gateway.pending_requests())

    def test_inbox_rejects_changed_policy_version(self):
        request_id = self.gateway.request_approval(self.request)
        self.gateway.policy_engine = ToolPolicyEngine((
            PolicyRule(
                "approval-required", Decision.REQUIRE_APPROVAL,
                capability="workspace:write", principal_id="alice",
                target="repo:project-a", risk=Risk.HIGH,
            ),
        ), policy_version="policy-v8")
        with self.assertRaises(PermissionError):
            self.gateway.approve_request(request_id, approved_by="admin")
        self.assertFalse(self.gateway.pending_requests())

    def test_inbox_expires_requests(self):
        request_id = self.gateway.request_approval(self.request, ttl_seconds=5)
        self.now[0] += 5
        self.assertFalse(self.gateway.pending_requests())
        with self.assertRaises(PermissionError):
            self.gateway.approve_request(request_id, approved_by="admin")

    def test_typed_operation_is_persisted_and_integrity_checked(self):
        payload = {"changes": [{
            "path": "README.md", "expected_sha256": "b" * 64,
            "content": "approved content",
        }]}
        operation = ApprovalOperation.from_payload(
            operation_type="workspace.write", capability="workspace:write",
            target="repo:project-a", payload=payload,
        )
        request = self.make_request(argument_sha256=argument_digest(payload))
        request_id = self.gateway.request_approval(request, operation=operation, operation_payload=payload)
        stored = self.gateway.approval_operation(request_id)
        self.assertEqual(stored["operation_type"], "workspace.write")
        self.assertEqual(stored["payload_sha256"], operation.payload_sha256)
        self.assertEqual(stored["target"], "repo:project-a")
        self.assertEqual(self.gateway.approval_operation_payload(request_id), payload)
        inbox_row = self.gateway.pending_requests()[0]
        self.assertEqual(inbox_row["operation"], stored)

        reopened = CapabilityApprovalGateway(
            Path(self.temp.name) / "approvals.sqlite", self.engine,
            authorize_approver=lambda actor, request: actor == "admin",
            clock=lambda: self.now[0], preview_registry=self.previews,
            seal_operation_payload=self.seal_payload,
            unseal_operation_payload=self.unseal_payload,
        )
        self.assertEqual(reopened.approval_operation(request_id), stored)
        self.assertEqual(reopened.approval_operation_payload(request_id), payload)

    def test_typed_operation_rejects_request_mismatch_and_idempotency_substitution(self):
        payload = {"changes": [{"path": "README.md", "content": "approved"}]}
        operation = ApprovalOperation.from_payload(
            operation_type="workspace.write", capability="workspace:write",
            target="repo:project-a", payload=payload,
        )
        with self.assertRaises(ValueError):
            self.gateway.request_approval(self.request, operation=operation)
        request = self.make_request(argument_sha256=argument_digest(payload))
        request_id = self.gateway.request_approval(request, operation=operation, operation_payload=payload)
        with self.assertRaises(PermissionError):
            self.gateway.request_approval(request)

    def test_capability_rejects_unregistered_typed_operation_kind(self):
        payload = {"changes": [{"path": "README.md", "content": "approved"}]}
        operation = ApprovalOperation.from_payload(
            operation_type="workspace.delete", capability="workspace:write",
            target="repo:project-a", payload=payload,
        )
        request = self.make_request(argument_sha256=argument_digest(payload))
        with self.assertRaises(PermissionError):
            self.gateway.request_approval(request, operation=operation, operation_payload=payload)

    def test_approval_credential_is_bound_to_typed_operation_envelope(self):
        payload = {"changes": [{"path": "README.md", "content": "approved"}]}
        operation = ApprovalOperation.from_payload(
            operation_type="workspace.write", capability="workspace:write",
            target="repo:project-a", payload=payload,
        )
        request = self.make_request(argument_sha256=argument_digest(payload))
        request_id = self.gateway.request_approval(request, operation=operation, operation_payload=payload)
        token = self.gateway.approve_request(request_id, approved_by="admin")
        self.assertTrue(self.gateway.authorize(
            request, approval_token=token, operation=operation
        ).allowed)
        # A credential bound to a typed envelope cannot be consumed through
        # the legacy untyped authorization route.
        request_id = self.gateway.request_approval(
            self.make_request(argument_sha256=argument_digest(payload),
                              run_id="run-2", step_id="step-2"),
            operation=operation, operation_payload=payload,
        )
        request2 = self.make_request(argument_sha256=argument_digest(payload),
                                     run_id="run-2", step_id="step-2")
        token2 = self.gateway.approve_request(request_id, approved_by="admin")
        self.assertFalse(self.gateway.authorize(
            request2, approval_token=token2
        ).allowed)
        altered = ApprovalOperation.from_payload(
            operation_type="workspace.delete", capability="workspace:write",
            target="repo:project-a", payload=payload,
        )
        with self.assertRaises(PermissionError):
            self.gateway.authorize(
                request2, approval_token=token2, operation=altered
            )

    def test_typed_operation_metadata_fails_closed_after_storage_tampering(self):
        payload = {"changes": [{"path": "README.md", "content": "approved"}]}
        operation = ApprovalOperation.from_payload(
            operation_type="workspace.write", capability="workspace:write",
            target="repo:project-a", payload=payload,
        )
        request = self.make_request(argument_sha256=argument_digest(payload))
        request_id = self.gateway.request_approval(request, operation=operation, operation_payload=payload)
        with closing(self.gateway._connect()) as db:
            db.execute(
                "UPDATE capability_approval_operations SET operation_json=? WHERE request_id=?",
                ('{"schema_version":1,"operation_type":"workspace.delete"}', request_id),
            )
            db.commit()
        self.assertIsNone(self.gateway.approval_operation(request_id))
        self.assertIsNone(self.gateway.approval_operation_payload(request_id))
        with self.assertRaises(PermissionError):
            self.gateway.approve_request(request_id, approved_by="admin")

    def test_typed_payload_storage_requires_encryption_adapter(self):
        payload = {"changes": [{"path": "README.md", "content": "approved"}]}
        operation = ApprovalOperation.from_payload(
            operation_type="workspace.write", capability="workspace:write",
            target="repo:project-a", payload=payload,
        )
        gateway = CapabilityApprovalGateway(
            Path(self.temp.name) / "no-encryption.sqlite", self.engine,
            authorize_approver=lambda actor, request: actor == "admin",
            clock=lambda: self.now[0], preview_registry=self.previews,
        )
        request = self.make_request(argument_sha256=argument_digest(payload))
        with self.assertRaises(PermissionError):
            gateway.request_approval(
                request, operation=operation, operation_payload=payload
            )

    def test_exact_typed_payload_tampering_blocks_approval(self):
        payload = {"changes": [{"path": "README.md", "content": "approved"}]}
        operation = ApprovalOperation.from_payload(
            operation_type="workspace.write", capability="workspace:write",
            target="repo:project-a", payload=payload,
        )
        request = self.make_request(argument_sha256=argument_digest(payload))
        request_id = self.gateway.request_approval(
            request, operation=operation, operation_payload=payload
        )
        with closing(self.gateway._connect()) as db:
            db.execute(
                "UPDATE capability_approval_operations SET payload_ciphertext=? WHERE request_id=?",
                (b"tampered-envelope-content", request_id),
            )
            db.commit()
        self.assertIsNone(self.gateway.approval_operation_payload(request_id))
        with self.assertRaises(PermissionError):
            self.gateway.approve_request(request_id, approved_by="admin")

    def test_inbox_rejects_non_approval_policy(self):
        allowed = self.make_request(capability="workspace:read")
        with self.assertRaises(PermissionError):
            self.gateway.request_approval(allowed)



if __name__ == "__main__":
    unittest.main()
