"""Integration tests for durable approval enforcement at skill dispatch."""
from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from core.approved_skill_runtime import ApprovedSkillRuntime
from core.approved_skill_wiring import SkillApprovalStore
from core.capability_approval_gateway import CapabilityApprovalGateway
from core.guarded_skill_runtime import SkillPolicyContext
from core.tool_policy import PolicyRequest, PolicyRule, Risk, Decision, ToolPolicyEngine, argument_digest


class Registry:
    def __init__(self, skill):
        self.skill = skill

    def get(self, name):
        return self.skill if name == self.skill.name else None


class SideEffectSkill:
    name = "weather"
    risk = "low"
    requires_approval = False

    def __init__(self):
        self.calls = []

    def execute(self, argument=""):
        self.calls.append(argument)
        return {"ok": True, "message": "executed"}


class ApprovalGatewaySkillDispatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.skill = SideEffectSkill()
        self.context = SkillPolicyContext(
            principal_id="alice", trigger="durable_task",
            goal_id="goal-1", task_id="task-1",
        )
        self.engine = ToolPolicyEngine((
            PolicyRule(
                "skill-approval", Decision.REQUIRE_APPROVAL,
                principal_id="alice", trigger="durable_task",
                capability="skill.invoke", target="weather", risk=Risk.LOW,
            ),
        ), policy_version="skill-policy-v1")
        self.gateway = CapabilityApprovalGateway(
            root / "gateway.sqlite", self.engine,
            authorize_approver=lambda actor, request: actor == "admin",
        )
        self.registry = Registry(self.skill)
        self.runtime = ApprovedSkillRuntime(
            self.registry, SkillApprovalStore(root / "skills.sqlite"),
            policy_engine=self.engine,
            policy_request_factory=self.make_request,
            approval_gateway=self.gateway,
        )

    @staticmethod
    def make_request(name, argument, skill, context):
        if context is None:
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
        )

    def test_exact_durable_approval_is_consumed_before_skill_dispatch(self):
        request = self.make_request("weather", "sunny", self.skill, self.context)
        token = self.gateway.issue_approval(request, approved_by="admin")
        result = self.runtime.invoke(
            "weather", "sunny", policy_context=self.context, approval_token=token,
        )
        self.assertTrue(result.ok)
        self.assertEqual(self.skill.calls, ["sunny"])
        replay = self.runtime.invoke(
            "weather", "sunny", policy_context=self.context, approval_token=token,
        )
        self.assertFalse(replay.ok)
        self.assertEqual(replay.status, "central_policy_approval_invalid")
        self.assertEqual(self.skill.calls, ["sunny"])

    def test_missing_or_mismatched_approval_never_invokes_skill(self):
        missing = self.runtime.invoke(
            "weather", "sunny", policy_context=self.context,
        )
        self.assertFalse(missing.ok)
        self.assertEqual(missing.status, "central_policy_require_approval")
        request = self.make_request("weather", "sunny", self.skill, self.context)
        token = self.gateway.issue_approval(request, approved_by="admin")
        changed = self.runtime.invoke(
            "weather", "rainy", policy_context=self.context, approval_token=token,
        )
        self.assertFalse(changed.ok)
        self.assertEqual(changed.status, "central_policy_approval_invalid")
        self.assertEqual(self.skill.calls, [])

    def test_gateway_failure_fails_closed(self):
        class BrokenGateway:
            def authorize(self, request, *, approval_token=None):
                raise RuntimeError("storage unavailable")

        runtime = ApprovedSkillRuntime(
            self.registry, self.runtime.approval_store,
            policy_engine=self.engine,
            policy_request_factory=self.make_request,
            approval_gateway=BrokenGateway(),
        )
        result = runtime.invoke("weather", "sunny", policy_context=self.context)
        self.assertFalse(result.ok)
        self.assertEqual(result.status, "central_policy_error")
        self.assertEqual(self.skill.calls, [])


if __name__ == "__main__":
    unittest.main()
