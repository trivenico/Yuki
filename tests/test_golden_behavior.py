"""Executable golden checks against real Yuki safety/runtime components.

These are deterministic offline tests: no model provider or network is called.
"""
import unittest

from core.agent import YukiCoreAgent
from core.output_guard import OutputGuard
from core.permissions import PermissionGate
from tests.golden_dataset import GOLDEN_CASES
from skills.base import Skill, SkillResult


class DeniedSkill(Skill):
    name = "golden_denied_skill"
    description = "Deterministic test skill"
    risk = "low"

    def run(self, argument=""):
        return SkillResult(True, "This must never execute.")


class GoldenBehaviorTests(unittest.TestCase):
    def test_dataset_has_unique_stable_ids_and_twenty_cases(self):
        self.assertGreaterEqual(len(GOLDEN_CASES), 20)
        ids = [case.case_id for case in GOLDEN_CASES]
        self.assertEqual(len(ids), len(set(ids)))

    def test_g014_unknown_skill_is_denied_by_real_agent(self):
        case = next(case for case in GOLDEN_CASES if case.case_id == "g014")
        response = YukiCoreAgent().run_skill("not-registered")
        self.assertFalse(response.ok)
        self.assertIn(case.expected_contains[0], response.message.lower())

    def test_g015_secret_output_is_withheld_by_real_guard(self):
        case = next(case for case in GOLDEN_CASES if case.case_id == "g015")
        response = OutputGuard().check("api_key=supersecret")
        self.assertFalse(response.allowed)
        self.assertIn(case.expected_contains[0], response.text.lower())
        self.assertNotIn("supersecret", response.text)

    def test_g017_oversized_output_is_truncated_by_real_guard(self):
        case = next(case for case in GOLDEN_CASES if case.case_id == "g017")
        response = OutputGuard(max_chars=10).check("x" * 100)
        self.assertIn(case.expected_contains[0], response.text.lower())
        self.assertIn("output_too_long", response.reasons)

    def test_g003_unapproved_skill_does_not_execute(self):
        ran = []
        class MustNotRun(Skill):
            name = "must_not_run"
            description = "Test that approval is enforced"
            risk = "low"
            def run(self, argument=""):
                ran.append(True)
                return SkillResult(True, "unexpected execution")

        agent = YukiCoreAgent(
            skills=[MustNotRun()],
            permission_gate=PermissionGate(lambda _request: False),
        )
        response = agent.run_skill("must_not_run")
        self.assertFalse(response.ok)
        self.assertEqual(response.stage, "permission_denied")
        self.assertEqual(ran, [])


if __name__ == "__main__":
    unittest.main()
