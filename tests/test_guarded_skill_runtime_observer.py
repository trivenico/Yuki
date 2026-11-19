import unittest

from core.guarded_skill_runtime import OneShotSkillApprovals, invoke_registered_skill


class Skill:
    name = "triage"
    risk = "low"
    requires_approval = False

    def __init__(self, order):
        self.order = order

    def execute(self, argument):
        self.order.append("execute")
        return "done"


class Registry:
    def __init__(self, skill):
        self.skill = skill

    def get(self, name):
        return self.skill if name == "triage" else None


class GuardedSkillObserverTests(unittest.TestCase):
    def test_observer_runs_after_validation_and_immediately_before_execute(self):
        order = []
        result = invoke_registered_skill(
            Registry(Skill(order)), "triage", "work",
            approvals=OneShotSkillApprovals(),
            before_execute=lambda: order.append("observer"),
        )
        self.assertTrue(result.ok)
        self.assertEqual(order, ["observer", "execute"])

    def test_observer_failure_fails_closed_without_executing_skill(self):
        order = []

        def fail():
            order.append("observer")
            raise RuntimeError("telemetry unavailable")

        result = invoke_registered_skill(
            Registry(Skill(order)), "triage", "work",
            approvals=OneShotSkillApprovals(),
            before_execute=fail,
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.status, "pre_execution_observer_failed")
        self.assertEqual(order, ["observer"])

    def test_invalid_skill_does_not_emit_observer(self):
        order = []
        result = invoke_registered_skill(
            Registry(Skill(order)), "missing", "work",
            approvals=OneShotSkillApprovals(),
            before_execute=lambda: order.append("observer"),
        )
        self.assertFalse(result.ok)
        self.assertEqual(order, [])
        self.assertEqual(result.status, "not_found")


if __name__ == "__main__":
    unittest.main()
