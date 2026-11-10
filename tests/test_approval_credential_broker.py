import hashlib
import tempfile
import unittest
from pathlib import Path

from core.approval_credential_broker import ProtectedApprovalCredentialBroker
from core.approved_skill_runtime import ApprovedSkillRuntime
from core.approved_skill_wiring import SkillApprovalStore
from core.capability_approval_gateway import CapabilityApprovalGateway
from core.guarded_skill_runtime import SkillPolicyContext
from core.tool_policy import Decision, PolicyRequest, PolicyRule, Risk, ToolPolicyEngine


class HighRiskSkill:
    name = "write_demo"
    risk = "high"
    requires_approval = True

    def __init__(self):
        self.calls = 0

    def execute(self, argument):
        self.calls += 1
        return {"ok": True, "message": "executed"}


class Registry:
    def __init__(self, skill):
        self.skill = skill

    def get(self, name):
        return self.skill if name == self.skill.name else None


class ProtectedApprovalCredentialBrokerTests(unittest.TestCase):
    def setUp(self):
        self.vault = {}

        def put_secret(key, secret, ttl_seconds):
            self.vault[key] = secret

        def consume_secret(key):
            return self.vault.pop(key, None)

        self.broker = ProtectedApprovalCredentialBroker(
            put_secret=put_secret, consume_secret=consume_secret,
            delete_secret=lambda key: self.vault.pop(key, None),
        )
        self.request = PolicyRequest(
            principal_id="alice", trigger="durable_task",
            capability="workspace:write", target="repo:project-a",
            argument_sha256=hashlib.sha256(b"change").hexdigest(),
            risk=Risk.HIGH, run_id="goal-1", step_id="task-1",
        )

    def test_secret_is_external_and_resolver_is_exact_and_one_shot(self):
        token = "t" * 43
        self.broker.deliver("a" * 32, self.request, token, 300)
        self.assertEqual(len(self.vault), 1)
        self.assertEqual(self.broker.resolve(self.request), token)
        self.assertIsNone(self.broker.resolve(self.request))
        self.assertEqual(self.vault, {})

    def test_secret_cannot_be_resolved_for_modified_request(self):
        token = "t" * 43
        self.broker.deliver("a" * 32, self.request, token, 300)
        altered = PolicyRequest(
            principal_id="alice", trigger="durable_task",
            capability="workspace:write", target="repo:project-a",
            argument_sha256=hashlib.sha256(b"other").hexdigest(),
            risk=Risk.HIGH, run_id="goal-1", step_id="task-1",
        )
        self.assertIsNone(self.broker.resolve(altered))
        self.assertEqual(self.broker.resolve(self.request), token)

    def test_gateway_approval_executes_high_risk_skill_exactly_once(self):
        with tempfile.TemporaryDirectory() as directory:
            policy = ToolPolicyEngine((
                PolicyRule(
                    "approval-required", Decision.REQUIRE_APPROVAL,
                    capability="workspace:write", principal_id="alice",
                    target="repo:project-a", risk=Risk.HIGH,
                ),
            ), policy_version="policy-v1")
            gateway = CapabilityApprovalGateway(
                Path(directory) / "gateway.sqlite", policy,
                authorize_approver=lambda actor, request: actor == "approver",
            )
            skill = HighRiskSkill()
            registry = Registry(skill)

            def request_factory(skill_name, argument, instance, context):
                self.assertEqual(skill_name, "write_demo")
                self.assertIs(instance, skill)
                return PolicyRequest(
                    principal_id=context.principal_id, trigger=context.trigger,
                    capability="workspace:write", target="repo:project-a",
                    argument_sha256=hashlib.sha256(argument.encode("utf-8")).hexdigest(),
                    risk=Risk.HIGH, run_id=context.goal_id, step_id=context.task_id,
                )

            context = SkillPolicyContext(
                principal_id="alice", trigger="durable_task",
                goal_id="goal-1", task_id="task-1",
            )
            request = request_factory("write_demo", "change", skill, context)
            request_id = gateway.request_approval(request)
            token = gateway.approve_request(request_id, approved_by="approver")
            self.broker.deliver(request_id, request, token, 300)
            runtime = ApprovedSkillRuntime(
                registry, SkillApprovalStore(Path(directory) / "skills.sqlite"),
                approval_gateway=gateway,
                policy_request_factory=request_factory,
                approval_token_resolver=self.broker.resolve,
            )
            result = runtime.invoke("write_demo", "change", policy_context=context)
            self.assertTrue(result.ok)
            self.assertEqual(result.status, "completed")
            self.assertEqual(skill.calls, 1)
            replay = runtime.invoke("write_demo", "change", policy_context=context)
            self.assertFalse(replay.ok)
            self.assertEqual(skill.calls, 1)


if __name__ == "__main__":
    unittest.main()
