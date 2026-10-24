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

    def test_each_call_requests_approval_again(self):
        requests = []
        skill = NormallyUnapprovedSkill()
        agent = YukiCoreAgent([skill], PermissionGate(lambda request: requests.append(request) or True))
        self.assertTrue(agent.run_skill(skill.name).ok)
        self.assertTrue(agent.run_skill(skill.name).ok)
        self.assertEqual(len(requests), 2)
        self.assertNotIn("argument=", requests[0].reason)
        self.assertIn("arguments are redacted", requests[0].reason)


    def test_approver_exception_fails_closed(self):
        def broken_approver(_request):
            raise RuntimeError("approval UI unavailable")

        gate = PermissionGate(broken_approver)
        self.assertFalse(gate.request("publish", "publish release", risk="high"))

    def test_truthy_non_boolean_approval_is_rejected(self):
        gate = PermissionGate(lambda _request: "yes")
        self.assertFalse(gate.request("delete", "delete file", risk="high"))

    def test_unhashable_and_non_string_risk_values_fail_closed(self):
        calls = []
        gate = PermissionGate(lambda request: calls.append(request) or True)
        for risk in ([], {}, set(), None, 1, True):
            with self.subTest(risk=type(risk).__name__):
                self.assertFalse(gate.request("publish", "publish release", risk=risk))
        self.assertEqual(calls, [])

    def test_invalid_requests_fail_without_calling_approver(self):
        calls = []
        gate = PermissionGate(lambda request: calls.append(request) or True)
        self.assertFalse(gate.request("", "reason"))
        self.assertFalse(gate.request("action", ""))
        self.assertFalse(gate.request("action", "reason", risk="critical"))
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
