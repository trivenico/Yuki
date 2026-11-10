"""End-to-end durable worker authorization through the encrypted broker."""
import hashlib
import hmac
from pathlib import Path
import tempfile
import unittest

from core.approval_credential_broker import ApprovalCredentialBroker
from core.approved_skill_runtime import ApprovedSkillRuntime
from core.approved_skill_wiring import SkillApprovalStore
from core.capability_approval_gateway import CapabilityApprovalGateway
from core.durable_skill_worker import DurableSkillWorker
from core.durable_task_engine import DurableTaskEngine
from core.guarded_skill_runtime import SkillPolicyContext
from core.tool_policy import Decision, PolicyRequest, PolicyRule, Risk, ToolPolicyEngine, argument_digest


_TEST_KEY = b"worker-integration-test-key"


def seal(plaintext: bytes, aad: bytes) -> bytes:
    # Test-only AEAD-shaped fixture. Production must inject a real KMS-backed AEAD.
    ciphertext = bytes(v ^ _TEST_KEY[i % len(_TEST_KEY)] for i, v in enumerate(plaintext))
    return hmac.new(_TEST_KEY, aad + ciphertext, hashlib.sha256).digest() + ciphertext


def unseal(envelope: bytes, aad: bytes) -> bytes:
    tag, ciphertext = envelope[:32], envelope[32:]
    expected = hmac.new(_TEST_KEY, aad + ciphertext, hashlib.sha256).digest()
    if not hmac.compare_digest(tag, expected):
        raise ValueError("invalid test envelope")
    return bytes(v ^ _TEST_KEY[i % len(_TEST_KEY)] for i, v in enumerate(ciphertext))


class Registry:
    def __init__(self, skill):
        self.skill = skill

    def get(self, name):
        return self.skill if name == self.skill.name else None


class WriteSkill:
    name = "write_demo"
    risk = "low"
    requires_approval = False

    def __init__(self):
        self.calls = []

    def execute(self, argument):
        self.calls.append(argument)
        return {"ok": True, "message": "write completed"}


class ApprovalBrokerWorkerIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.engine = DurableTaskEngine(root / "tasks.sqlite")
        self.addCleanup(self.engine.close)
        self.goal = self.engine.create_goal(owner_id="alice")
        self.skill = WriteSkill()
        self.registry = Registry(self.skill)
        self.policy = ToolPolicyEngine((
            PolicyRule(
                "require-write-approval", Decision.REQUIRE_APPROVAL,
                principal_id="alice", trigger="durable_task",
                capability="skill.invoke", target="write_demo", risk=Risk.LOW,
            ),
        ), policy_version="worker-approval-v1")
        self.gateway = CapabilityApprovalGateway(
            root / "gateway.sqlite", self.policy,
            authorize_approver=lambda actor, request: actor == "approver",
        )
        self.broker = ApprovalCredentialBroker(
            root / "broker.sqlite", self.gateway, worker_id="worker-1",
            seal=seal, unseal=unseal,
        )
        self.runtime = ApprovedSkillRuntime(
            self.registry, SkillApprovalStore(root / "skills.sqlite"),
            policy_engine=self.policy,
            policy_request_factory=self.make_request,
            approval_gateway=self.gateway,
        )

    @staticmethod
    def make_request(name, argument, skill, context):
        if context is None:
            raise ValueError("trusted task context required")
        return PolicyRequest(
            principal_id=context.principal_id, trigger=context.trigger,
            capability="skill.invoke", target=name,
            argument_sha256=argument_digest({"argument": argument}),
            risk=Risk.LOW, run_id=context.goal_id, step_id=context.task_id,
        )

    def submit(self, argument):
        return self.engine.submit(
            self.goal, "skill.invoke",
            {"skill_name": "write_demo", "argument": argument},
            idempotency_key="write-" + argument, max_attempts=1, owner_id="alice",
        )

    def test_approved_credential_flows_to_exact_worker_and_dispatch(self):
        task = self.submit("approved-value")
        context = SkillPolicyContext("alice", "durable_task", self.goal, task["id"])
        request = self.make_request("write_demo", "approved-value", self.skill, context)
        request_id = self.gateway.request_approval(request)
        token = self.gateway.approve_request(request_id, approved_by="approver")
        self.broker.deliver_credential(request_id, token)

        worker = DurableSkillWorker(
            self.engine, self.runtime, worker_id="worker-1", owner_id="alice",
            approval_token_resolver=self.broker.resolver(),
        )
        result = worker.run_once()
        self.assertEqual(result["state"], "succeeded")
        self.assertEqual(self.skill.calls, ["approved-value"])
        stored = self.engine.get_task(task["id"])
        self.assertNotIn(token, repr(stored))
        self.assertNotIn("approval_token", stored["payload"])

    def test_mismatched_task_argument_is_blocked_before_side_effect(self):
        task = self.submit("tampered-value")
        context = SkillPolicyContext("alice", "durable_task", self.goal, task["id"])
        request = self.make_request("write_demo", "original-approved-value", self.skill, context)
        request_id = self.gateway.request_approval(request)
        token = self.gateway.approve_request(request_id, approved_by="approver")
        self.broker.deliver_credential(request_id, token)
        result = DurableSkillWorker(
            self.engine, self.runtime, worker_id="worker-1", owner_id="alice",
            approval_token_resolver=self.broker.resolver(),
        ).run_once()
        self.assertEqual(result["state"], "failed")
        self.assertEqual(self.skill.calls, [])

    def test_gateway_runtime_cannot_be_constructed_without_resolver(self):
        task = self.submit("needs-approval")
        with self.assertRaisesRegex(ValueError, "approval_token_resolver"):
            DurableSkillWorker(
                self.engine, self.runtime, worker_id="worker-1", owner_id="alice",
            )

    def test_worker_rejects_resolver_bound_to_another_worker(self):
        with self.assertRaisesRegex(ValueError, "different worker identity"):
            DurableSkillWorker(
                self.engine, self.runtime, worker_id="worker-2", owner_id="alice",
                approval_token_resolver=self.broker.resolver(),
            )


if __name__ == "__main__":
    unittest.main()
