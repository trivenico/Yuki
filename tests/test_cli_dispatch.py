import unittest
from unittest.mock import patch

import main


class CliDispatchRegressionTests(unittest.TestCase):
    def test_empty_skill_commands_return_usage_parse(self):
        self.assertEqual(main.parse_skill_command("/skill"), (None, ""))
        self.assertEqual(main.parse_skill_command("/skill "), (None, ""))

    def test_valid_skill_command_preserves_argument(self):
        self.assertEqual(main.parse_skill_command("/skill time now"), ("time", "now"))

    def test_malformed_evolution_review_is_safe(self):
        self.assertEqual(main.parse_evolution_review_command("/evolution review"), (None, None))
        self.assertEqual(main.parse_evolution_review_command("/evolution review id"), (None, None))
        self.assertEqual(main.parse_evolution_review_command("/evolution review id accept"), (None, None))
        self.assertEqual(main.parse_evolution_review_command("/evolution review id accept"), (None, None))
        self.assertEqual(main.parse_evolution_review_command("/evolution review id accept"), (None, None))
        self.assertEqual(main.parse_evolution_review_command("/evolution review id accept"), (None, None))
        self.assertEqual(main.parse_evolution_review_command("/evolution review id accept"), (None, None))
        self.assertEqual(main.parse_evolution_review_command("/evolution review id accept"), (None, None))
        self.assertEqual(main.parse_evolution_review_command("/evolution review proposal-1 accept"), ("proposal-1", "accept"))
        self.assertEqual(main.parse_evolution_review_command("/evolution review proposal-1 reject"), ("proposal-1", "reject"))

    def test_config_error_is_reported_without_traceback(self):
        with patch.object(main, "load_config", side_effect=RuntimeError("bad local config")):
            with patch("builtins.print") as output:
                main.main()
        rendered = " ".join(str(call.args[0]) for call in output.call_args_list)
        self.assertIn("Configuration unavailable", rendered)
        self.assertNotIn("bad local config", rendered)

    def test_agent_startup_error_is_friendly(self):
        config = {"workspace": "/tmp/yuki-test-startup"}
        with patch.object(main, "load_config", return_value=config), patch.object(main.Path, "mkdir", side_effect=OSError("private path")):
            with patch("builtins.print") as output:
                main.main()
        rendered = " ".join(str(call.args[0]) for call in output.call_args_list)
        self.assertIn("Agent startup failed safely", rendered)
        self.assertNotIn("private path", rendered)


if __name__ == "__main__":
    unittest.main()
