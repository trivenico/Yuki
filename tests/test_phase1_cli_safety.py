import unittest
from unittest.mock import patch

import main


class PhaseOneCliSafetyTests(unittest.TestCase):
    def test_skill_parser_handles_missing_name_and_argument(self):
        self.assertEqual(main.parse_skill_command("/skill"), (None, ""))
        self.assertEqual(main.parse_skill_command("/skill "), (None, ""))
        self.assertEqual(main.parse_skill_command("/skill notes.write"), ("notes.write", ""))
        self.assertEqual(main.parse_skill_command("/skill notes.write hello world"),
                         ("notes.write", "hello world"))

    def test_skill_command_handles_missing_name_without_calling_agent(self):
        agent = unittest.mock.Mock()
        output = []
        self.assertFalse(main.run_skill_command(agent, "/skill", output.append))
        self.assertIn("Provide a skill name", output[0])
        agent.run_skill.assert_not_called()

    def test_skill_exception_is_sanitized_and_local_to_command(self):
        agent = unittest.mock.Mock()
        agent.run_skill.side_effect = RuntimeError("private skill details")
        output = []
        self.assertFalse(main.run_skill_command(agent, "/skill notes.write", output.append))
        self.assertIn("Skill command failed safely (RuntimeError)", output[0])
        self.assertNotIn("private skill details", output[0])

    def test_skill_success_emits_message_and_data(self):
        response = unittest.mock.Mock(ok=True, message="Completed", data={"saved": True})
        agent = unittest.mock.Mock()
        agent.run_skill.return_value = response
        output = []
        self.assertTrue(main.run_skill_command(agent, "/skill notes.write hello", output.append))
        agent.run_skill.assert_called_once_with("notes.write", "hello")
        self.assertEqual(output, ["Yuki > Completed", {"saved": True}])

    def test_configuration_startup_failure_is_sanitized(self):
        with patch.object(main, "load_config", side_effect=RuntimeError("private config path")):
            with patch("builtins.print") as output:
                main.main()
        rendered = " ".join(str(call.args[0]) for call in output.call_args_list)
        self.assertIn("Configuration unavailable (RuntimeError)", rendered)
        self.assertNotIn("private config path", rendered)

    def test_agent_startup_failure_is_sanitized(self):
        config = {"workspace": "/tmp/yuki-phase1-test"}
        with patch.object(main, "load_config", return_value=config), \
             patch.object(main.Path, "mkdir", side_effect=OSError("private filesystem detail")):
            with patch("builtins.print") as output:
                main.main()
        rendered = " ".join(str(call.args[0]) for call in output.call_args_list)
        self.assertIn("Agent startup failed safely (OSError)", rendered)
        self.assertNotIn("private filesystem detail", rendered)

    def test_provider_startup_failure_is_sanitized(self):
        config = {"workspace": "/tmp/yuki-phase1-test", "max_agent_steps": 2}
        with patch.object(main, "load_config", return_value=config), \
             patch.object(main.Path, "mkdir"), \
             patch.object(main, "build_agent", return_value=object()), \
             patch.object(main, "create_provider", side_effect=RuntimeError("secret provider detail")):
            with patch("builtins.print") as output:
                main.main()
        rendered = " ".join(str(call.args[0]) for call in output.call_args_list)
        self.assertIn("Provider startup failed safely (RuntimeError)", rendered)
        self.assertNotIn("secret provider detail", rendered)


if __name__ == "__main__":
    unittest.main()
