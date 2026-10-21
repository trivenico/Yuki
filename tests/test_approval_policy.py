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

    def test_unhashable_risk_value_fails_closed(self):
        gate = PermissionGate(lambda _request: True)
        self.assertFalse(gate.request("shell", "run command", risk=[]))

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
