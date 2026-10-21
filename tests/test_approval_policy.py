import unittest

from core.agent import YukiCoreAgent
from core.permissions import PermissionGate
from skills.base import Skill, SkillResult


class NormallyUnapprovedSkill(Skill):
    name = "test_state_change"
    description = "Test state-changing operation"
    requires_approval = False

    def __init__(self):
        self.executed = False

    def run(self, argument=""):
        self.executed = True
        return SkillResult(True, "changed")


class StrictApprovalPolicyTests(unittest.TestCase):
    def test_skill_cannot_bypass_fresh_approval_by_setting_flag_false(self):
        skill = NormallyUnapprovedSkill()
        agent = YukiCoreAgent([skill], PermissionGate())
        result = agent.run_skill(skill.name)
        self.assertFalse(result.ok)
        self.assertEqual(result.stage, "permission_denied")
        self.assertFalse(skill.executed)

    def test_malformed_risk_values_fail_closed_without_calling_approver(self):
        calls = []
        gate = PermissionGate(lambda request: calls.append(request) or True)
        for risk in ([], {}, set(), None, 1, True):
            with self.subTest(risk=repr(risk)):
                self.assertFalse(gate.request("shell.execute", "run untrusted command", risk=risk))
        self.assertEqual(calls, [])

    def test_approver_must_return_exact_true(self):
        for response in (1, "yes", [], None):
            with self.subTest(response=repr(response)):
                gate = PermissionGate(lambda _request, value=response: value)
                self.assertFalse(gate.request("shell.execute", "run command", risk="high"))

    def test_each_call_requests_approval_again(self):
        requests = []
        skill = NormallyUnapprovedSkill()
        agent = YukiCoreAgent([skill], PermissionGate(lambda request: requests.append(request) or True))
        self.assertTrue(agent.run_skill(skill.name).ok)
        self.assertTrue(agent.run_skill(skill.name).ok)
        self.assertEqual(len(requests), 2)
        self.assertIn("argument=", requests[0].reason)


if __name__ == "__main__":
    unittest.main()
