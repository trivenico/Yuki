import unittest

from main import parse_skill_command


class SkillCommandParsingTests(unittest.TestCase):
    def test_empty_skill_commands_return_usage_parse(self):
        self.assertEqual(parse_skill_command("/skill"), (None, ""))
        self.assertEqual(parse_skill_command("/skill "), (None, ""))

    def test_valid_skill_command_preserves_argument(self):
        self.assertEqual(parse_skill_command("/skill time now"), ("time", "now"))
        self.assertEqual(parse_skill_command("/skill file.read folder with spaces"),
                         ("file.read", "folder with spaces"))

    def test_unrelated_commands_are_not_parsed_as_skills(self):
        self.assertEqual(parse_skill_command("/skills"), (None, ""))
        self.assertEqual(parse_skill_command("/skillful test"), (None, ""))
        self.assertEqual(parse_skill_command(None), (None, ""))


if __name__ == "__main__":
    unittest.main()
