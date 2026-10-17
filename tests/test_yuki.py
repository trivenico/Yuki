import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yuki_learning
import yuki_tasks
import yuki_security
from providers import MockProvider
from yuki_agent import YukiAgent
from yuki_tools import TOOLS, calculate, run_tool
from yuki_security import SecurityPolicy, write_audit_event


class ProviderTests(unittest.TestCase):
    def test_mock_provider_echoes_prompt(self):
        self.assertIn("hello", MockProvider().generate("hello"))

    def test_agent_rejects_empty_prompt(self):
        self.assertEqual(YukiAgent().ask("   "), "Yuki: Please enter a message.")

    def test_agent_handles_provider_exception(self):
        class BrokenProvider:
            def is_available(self):
                raise RuntimeError("private provider details")
            def generate(self, prompt):
                raise AssertionError("should not be called")
        self.assertEqual(YukiAgent(BrokenProvider()).ask("hello"),
                         "Yuki: The provider encountered an error.")


class ToolTests(unittest.TestCase):
    def test_calculator_handles_arithmetic(self):
        self.assertEqual(calculate("12 * (3 + 4)"), "84")

    def test_calculator_rejects_code(self):
        self.assertIn("Could not calculate", calculate("__import__('os').system('echo unsafe')"))

    def test_calculator_rejects_boolean(self):
        self.assertIn("Could not calculate", calculate("True + 1"))

    def test_unknown_tool_is_rejected(self):
        self.assertIn("Unknown tool", run_tool("shell", "whoami"))


class SecurityTests(unittest.TestCase):
    def test_policy_defaults_to_allow_list(self):
        policy = SecurityPolicy(allowed_tools=TOOLS.keys())
        self.assertTrue(policy.authorize_tool("calculate"))
        self.assertFalse(policy.authorize_tool("shell"))
        self.assertFalse(policy.authorize_tool("../calculate"))

    def test_audit_log_stores_metadata_only_and_is_bounded(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "audit.json"
            with patch.object(yuki_security, "AUDIT_FILE", path):
                self.assertTrue(write_audit_event("tool:calculate", "completed"))
                data = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(data[0]["action"], "tool:calculate")
                self.assertNotIn("argument", data[0])
                self.assertNotIn("secret", data[0])


class TaskTests(unittest.TestCase):
    def test_tasks_persist_and_complete(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "tasks.json"
            with patch.object(yuki_tasks, "TASKS_FILE", path):
                tasks = []
                self.assertEqual(yuki_tasks.add_task(tasks, "Test task"), "Task added.")
                self.assertEqual(yuki_tasks.complete_task(tasks, 1), "Task completed.")
                self.assertTrue(yuki_tasks.load_tasks()[0]["completed"])

    def test_corrupt_task_file_returns_empty_list(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "tasks.json"
            path.write_text("{broken", encoding="utf-8")
            with patch.object(yuki_tasks, "TASKS_FILE", path):
                self.assertEqual(yuki_tasks.load_tasks(), [])


class LearningTests(unittest.TestCase):
    def test_outcome_is_saved_and_matching_lesson_found(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "learning.json"
            with patch.object(yuki_learning, "HISTORY_FILE", path):
                result = yuki_learning.record_outcome(
                    "Organize project files", "success",
                    "Group files by type", "Group related files before reviewing them"
                )
                self.assertIn("recorded", result.lower())
                self.assertTrue(yuki_learning.suggest_lessons("Organize project files"))

    def test_learning_redacts_key_like_text(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "learning.json"
            with patch.object(yuki_learning, "HISTORY_FILE", path):
                yuki_learning.record_outcome("test", "api_key=supersecretvalue123")
                saved = path.read_text(encoding="utf-8")
                self.assertNotIn("supersecretvalue123", saved)
                self.assertIn("[REDACTED]", saved)


if __name__ == "__main__":
    unittest.main()
