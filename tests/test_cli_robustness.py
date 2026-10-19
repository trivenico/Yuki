import unittest
from unittest.mock import patch

import main


class CliRobustnessTests(unittest.TestCase):
    def test_skill_command_without_name_is_safe(self):
        self.assertEqual(main.parse_skill_command("/skill "), (None, ""))
        self.assertEqual(main.parse_skill_command("/skill"), (None, ""))

    def test_skill_command_parses_name_and_argument(self):
        self.assertEqual(main.parse_skill_command("/skill notes.write hello world"), ("notes.write", "hello world"))
        self.assertEqual(main.parse_skill_command("/skill notes.write"), ("notes.write", ""))

    def test_config_error_is_reported_without_traceback(self):
        with patch.object(main, "load_config", side_effect=RuntimeError("secret local path")):
            with patch("builtins.print") as output:
                main.main()
        rendered = " ".join(str(call.args[0]) for call in output.call_args_list)
        self.assertIn("Configuration unavailable", rendered)
        self.assertNotIn("secret local path", rendered)


if __name__ == "__main__":
    unittest.main()
