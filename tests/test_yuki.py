import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yuki_learning
import yuki_tasks
from providers import MockProvider
from yuki_agent import YukiAgent
from yuki_tools import calculate, run_tool


class ProviderTests(unittest.TestCase):
    def test_mock_provider_echoes_prompt(self):
        self.assertIn("hello", MockProvider().generate("hello"))

    def test_agent_rejects_empty_prompt(self):
        self.assertEqual(YukiAgent().ask("   "), "Yuki: Please enter a message.")


class ToolTests(unittest.TestCase):
    def test_calculator_handles_arithmetic(self):
        self.assertEqual(calculate("12 * (3 + 4)"), "84")

    def test_calculator_rejects_code(self):
        self.assertIn("Could not calculate", calculate("__import__('os').system('echo unsafe')"))

    def test_unknown_tool_is_rejected(self):
        self.assertIn("Unknown tool", run_tool("shell", "whoami"))


class TaskTests(unittest.TestCase):
    def test_tasks_persist_and_complete(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "tasks.json"
            with patch.object(yuki_tasks, "TASKS_FILE", path):
                tasks = []
                self.assertEqual(yuki_tasks.add_task(tasks, "Test task"), "Task added.")
                self.assertEqual(yuki_tasks.complete_task(tasks, 1), "Task completed.")
                self.assertTrue(yuki_tasks.load_tasks()[0]["completed"])


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


if __name__ == "__main__":
    unittest.main()
