"""Regression tests for the shared skill argument boundary."""

import unittest

from skills.base import Skill, SkillResult


class RecordingSkill(Skill):
    name = "recording"
    max_argument_length = 5

    def __init__(self):
        self.received = []

    def run(self, argument=""):
        self.received.append(argument)
        return SkillResult(True, "executed")


class SkillBaseArgumentTests(unittest.TestCase):
    def test_argument_at_limit_is_passed_unchanged(self):
        skill = RecordingSkill()
        result = skill.execute("12345")
        self.assertTrue(result.ok)
        self.assertEqual(skill.received, ["12345"])

    def test_argument_over_limit_is_rejected_instead_of_truncated(self):
        skill = RecordingSkill()
        result = skill.execute("123456")
        self.assertFalse(result.ok)
        self.assertIn("5-character limit", result.message)
        self.assertEqual(skill.received, [])

    def test_invalid_argument_type_is_rejected(self):
        skill = RecordingSkill()
        result = skill.execute(None)
        self.assertFalse(result.ok)
        self.assertEqual(skill.received, [])


if __name__ == "__main__":
    unittest.main()
