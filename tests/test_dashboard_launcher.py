import json
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import run_dashboard


class DashboardLauncherTests(unittest.TestCase):
    def test_main_launches_textual_dashboard(self):
        with patch("ui.dashboard.run_dashboard") as dashboard:
            self.assertEqual(run_dashboard.main(), 0)
            dashboard.assert_called_once_with()

    def test_dashboard_dependency_error_is_reported_without_traceback(self):
        output = []
        with patch("ui.dashboard.run_dashboard", side_effect=RuntimeError("Install optional Textual")):
            with patch("builtins.print", side_effect=lambda *args, **kwargs: output.append(args[0])):
                self.assertEqual(run_dashboard.main(), 2)
        self.assertIn("Install optional Textual", output[0])

    def test_vscode_compound_task_starts_cli_and_dashboard_in_parallel(self):
        task_file = Path(__file__).resolve().parents[1] / ".vscode" / "tasks.json"
        config = json.loads(task_file.read_text(encoding="utf-8"))
        tasks = {item["label"]: item for item in config["tasks"]}
        combined = tasks["Yuki: Start CLI + Dashboard"]
        self.assertEqual(combined["dependsOrder"], "parallel")
        self.assertEqual(set(combined["dependsOn"]), {"Yuki CLI", "Yuki Dashboard"})
        self.assertEqual(tasks["Yuki CLI"]["args"], ["yuki.py"])
        self.assertEqual(tasks["Yuki Dashboard"]["args"], ["-m", "scripts.run_dashboard"])
        self.assertEqual(tasks["Yuki CLI"]["presentation"]["panel"], "dedicated")
        self.assertEqual(tasks["Yuki Dashboard"]["presentation"]["panel"], "dedicated")


if __name__ == "__main__":
    unittest.main()
