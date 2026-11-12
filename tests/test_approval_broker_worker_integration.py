"""Claim-bound approval broker to durable worker integration tests.

The envelope below is test-only. Production must inject a reviewed AEAD/KMS adapter.
"""
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

_KEY = b"unit-test-only-key"


def seal(plaintext: bytes, aad: bytes) -> bytes:
    ciphertext = bytes(value ^ _KEY[index % len(_KEY)] for index, value in enumerate(plaintext))
    return hmac.new(_KEY, aad + ciphertext, hashlib.sha256).digest() + ciphertext


def unseal(envelope: bytes, aad: bytes) -> bytes:
    tag, ciphertext = envelope[:32], envelope[32:]
    if not hmac.compare_digest(tag, hmac.new(_KEY, aad + ciphertext, hashlib.sha256).digest()):
        raise ValueError("invalid test envelope")
    return bytes(value ^ _KEY[index % len(_KEY)] for index, value in enumerate(ciphertext))


class Skill:
    name = "write_demo"
    risk = "low"
    requires_approval = False

    def __init__(self):
        self.calls = []

    def execute(self, argument):
        self.calls.append(argument)
        return {"ok": True, "message": "completed"}


class Registry:
    def __init__(self, skill):
        self.skill = skill

    def get(self, name):
        return self.skill if name == self.skill.name else None


class ClaimBoundApprovalIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.engine = DurableTaskEngine(root / "tasks.sqlite")
        self.addCleanup(self.engine.close)
        self.owner = "alice"
        self.goal = self.engine.create_owned_goal(self.owner)
        self.skill = Skill()
        self.policy = ToolPolicyEngine((
            PolicyRule(
                "approval-required", Decision.REQUIRE_APPROVAL,
                principal_id=self.owner, trigger="durable_task",
                capability="skill.invoke", target=self.skill.name, risk=Risk.LOW,
            ),
        ), policy_version="claim-bound-v1")
        self.gateway = CapabilityApprovalGateway(
            root / "gateway.sqlite", self.policy,
            authorize_approver=lambda actor, request: actor == "owner-approved-approver",
        )
        self.worker_id = "worker-1"
        self.runtime = ApprovedSkillRuntime(
            Registry(self.skill),
            SkillApprovalStore(root / "skills.sqlite"),
            policy_engine=self.policy,
            policy_request_factory=self.make_request,
            approval_gateway=self.gateway,
        )
        self.task = self.engine.submit(
            self.goal, "skill.invoke",
            {"skill_name": self.skill.name, "argument": "approved-value"},
            idempotency_key="claim-bound-approval", max_attempts=1,
            owner_id=self.owner,
        )
        self.claim = self.engine.claim(self.worker_id, lease_seconds=60)
        self.assertEqual(self.claim["id"], self.task["id"])
        self.lease_token = self.claim["lease_token"]
        self.context = SkillPolicyContext(
            self.owner, "durable_task", self.goal, self.task["id"],
            hashlib.sha256(self.lease_token.encode("ascii")).hexdigest(),
        )
        self.request = self.make_request(
            self.skill.name, "approved-value", self.skill, self.context
        )
        self.request_id = self.gateway.request_approval(self.request)
        self.approval_token = self.gateway.approve_request(
            self.request_id, approved_by="owner-approved-approver"
        )
        self.broker = ApprovalCredentialBroker(
            root / "broker.sqlite", self.gateway, worker_id=self.worker_id,
            seal=seal, unseal=unseal, validate_claim=self.engine.validate_lease,
        )
        self.broker.deliver_credential(
            self.request_id, self.approval_token, lease_token=self.lease_token
        )

    @staticmethod
    def make_request(name, argument, skill, context):
        if not isinstance(context, SkillPolicyContext):
            raise ValueError("trusted durable task context required")
        return PolicyRequest(
            principal_id=context.principal_id,
            trigger=context.trigger,
            capability="skill.invoke",
            target=name,
            argument_sha256=argument_digest({"argument": argument}),
            risk=Risk.LOW,
            run_id=context.goal_id,
            step_id=context.task_id,
            claim_token_sha256=context.claim_token_sha256,
        )

    def worker(self):
        # The task is already claimed so the test supplies that exact claim once.
        self.engine.claim = lambda *_args, **_kwargs: self.claim
        return DurableSkillWorker(
            self.engine, self.runtime, worker_id=self.worker_id,
            approval_token_resolver=self.broker.resolver(),
        )

    def test_exact_active_claim_approval_reaches_skill_once(self):
        result = self.worker().run_once()
        self.assertEqual(result["state"], "succeeded")
        self.assertEqual(self.skill.calls, ["approved-value"])
        stored = self.engine.get_task(self.task["id"])
        self.assertNotIn(self.approval_token, repr(stored))
        self.assertNotIn("approval_token", stored["payload"])

    def test_credential_cannot_be_resolved_with_another_claim_token(self):
        resolver = self.broker.resolver()
        self.assertIsNone(resolver(self.context, self.skill.name, "x" * 48))
        token = resolver(self.context, self.skill.name, self.lease_token)
        self.assertEqual(token, self.approval_token)
        self.assertIsNone(resolver(self.context, self.skill.name, self.lease_token))

    def test_claim_token_is_not_stored_in_broker_database(self):
        import sqlite3
        with sqlite3.connect(self.broker.database_path) as db:
            row = db.execute(
                "SELECT claim_token_sha256,ciphertext FROM approval_worker_credentials"
            ).fetchone()
        self.assertEqual(row[0], hashlib.sha256(self.lease_token.encode("ascii")).hexdigest())
        self.assertNotIn(self.lease_token.encode("ascii"), bytes(row[1]))

    def test_approval_token_cannot_be_delivered_to_a_different_claim_generation(self):
        with self.assertRaises(PermissionError):
            self.broker.deliver_credential(
                self.request_id, self.approval_token, lease_token="different-claim-token-" + "x" * 32
            )

    def test_broker_refuses_delivery_for_stale_claim(self):
        self.assertTrue(self.engine.cancel_task_for_owner(
            self.owner, self.task["id"]
        ))
        with self.assertRaises(PermissionError):
            self.broker.deliver_credential(
                self.request_id, self.approval_token, lease_token=self.lease_token
            )

    def test_runtime_requires_resolver_when_central_gateway_is_enabled(self):
        with self.assertRaisesRegex(ValueError, "approval_token_resolver"):
            DurableSkillWorker(self.engine, self.runtime, worker_id=self.worker_id)


if __name__ == "__main__":
    unittest.main()
