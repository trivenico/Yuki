import hashlib
import hmac
import tempfile
import unittest
from pathlib import Path

from core.approval_credential_broker import ApprovalCredentialBroker
from core.capability_approval_gateway import CapabilityApprovalGateway
from core.guarded_skill_runtime import SkillPolicyContext
from core.tool_policy import Decision, PolicyRequest, PolicyRule, Risk, ToolPolicyEngine


_TEST_KEY = b"test-only-envelope-key"


def _test_seal(plaintext: bytes, aad: bytes) -> bytes:
    # Test fixture only. Production must inject a KMS-backed AEAD adapter.
    ciphertext = bytes(value ^ _TEST_KEY[index % len(_TEST_KEY)] for index, value in enumerate(plaintext))
    tag = hmac.new(_TEST_KEY, aad + ciphertext, hashlib.sha256).digest()
    return tag + ciphertext


def _test_unseal(envelope: bytes, aad: bytes) -> bytes:
    # Test fixture only. Production must inject a KMS-backed AEAD adapter.
    tag, ciphertext = envelope[:32], envelope[32:]
    expected = hmac.new(_TEST_KEY, aad + ciphertext, hashlib.sha256).digest()
    if not hmac.compare_digest(tag, expected):
        raise ValueError("invalid test envelope")
    return bytes(value ^ _TEST_KEY[index % len(_TEST_KEY)] for index, value in enumerate(ciphertext))


class ApprovalCredentialBrokerTests(unittest.TestCase):
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
            Path(self.temp.name) / "gateway.sqlite", policy,
            authorize_approver=lambda actor, request: actor == "approver",
        )
        self.request = PolicyRequest(
            principal_id="alice", trigger="durable_task",
            capability="workspace:write", target="repo:project-a",
            argument_sha256="a" * 64, risk=Risk.HIGH,
            run_id="goal-1", step_id="task-1",
        )
        self.request_id = self.gateway.request_approval(self.request)
        self.token = self.gateway.approve_request(self.request_id, approved_by="approver")
        self.broker_path = Path(self.temp.name) / "broker.sqlite"
        self.broker = ApprovalCredentialBroker(
            self.broker_path, self.gateway, worker_id="worker-1",
            seal=_test_seal, unseal=_test_unseal,
        )

    def test_delivery_encrypts_at_rest_and_resolves_once(self):
        self.broker.deliver_credential(self.request_id, self.token)
        self.assertNotIn(self.token.encode("ascii"), self.broker_path.read_bytes())
        resolve = self.broker.resolver()
        context = SkillPolicyContext("alice", "durable_task", "goal-1", "task-1")
        self.assertEqual(resolve(context, "workspace-writer"), self.token)
        self.assertIsNone(resolve(context, "workspace-writer"))

    def test_context_and_worker_identity_are_bound(self):
        self.broker.deliver_credential(self.request_id, self.token)
        resolve = self.broker.resolver()
        self.assertIsNone(resolve(
            SkillPolicyContext("mallory", "durable_task", "goal-1", "task-1"),
            "workspace-writer",
        ))
        self.assertIsNone(resolve(
            SkillPolicyContext("alice", "durable_task", "goal-2", "task-1"),
            "workspace-writer",
        ))
        self.assertIsNone(resolve(
            SkillPolicyContext("alice", "durable_task", "goal-1", "task-2"),
            "workspace-writer",
        ))
        with self.assertRaises(PermissionError):
            self.broker.resolver("worker-2")

    def test_delivery_rejects_token_not_issued_for_request(self):
        with self.assertRaises(PermissionError):
            self.broker.deliver_credential(self.request_id, "x" * 48)

    def test_duplicate_delivery_is_rejected(self):
        self.broker.deliver_credential(self.request_id, self.token)
        with self.assertRaises(PermissionError):
            self.broker.deliver_credential(self.request_id, self.token)

    def test_expired_credential_cannot_be_delivered(self):
        self.gateway.clock = lambda: 10**12
        with self.assertRaises(PermissionError):
            self.broker.deliver_credential(self.request_id, self.token)


if __name__ == "__main__":
    unittest.main()
