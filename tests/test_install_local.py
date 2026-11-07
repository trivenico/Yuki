"""Tests for isolated local installer interpreter selection and failures."""
import importlib.util
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "install_local.py"
SPEC = importlib.util.spec_from_file_location("yuki_install_local", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class InstallLocalTests(unittest.TestCase):
    def test_uses_requested_python_executable_to_create_venv(self):
        version = subprocess.CompletedProcess([], 0, stdout="3.12\n", stderr="")
        with patch("sys.argv", ["install_local.py", "--python", "python3.12", "--skip-install"]):
            with patch.object(MODULE.subprocess, "run", side_effect=[version, None]) as run:
                self.assertEqual(MODULE.main(), 0)
        self.assertEqual(run.call_count, 2)
        self.assertEqual(run.call_args_list[0].args[0][0], "python3.12")
        self.assertEqual(run.call_args_list[1].args[0][:3], ["python3.12", "-m", "venv"])

    def test_rejects_selected_python_below_minimum_before_creating_venv(self):
        version = subprocess.CompletedProcess([], 0, stdout="3.9\n", stderr="")
        with patch("sys.argv", ["install_local.py", "--python", "python3.9", "--skip-install"]):
            with patch.object(MODULE.subprocess, "run", return_value=version) as run:
                self.assertEqual(MODULE.main(), 1)
        run.assert_called_once()
        self.assertIn("-c", run.call_args.args[0])

    def test_venv_creation_failure_is_reported(self):
        version = subprocess.CompletedProcess([], 0, stdout="3.11\n", stderr="")
        with patch("sys.argv", ["install_local.py", "--python", "missing-python", "--skip-install"]):
            with patch.object(MODULE.subprocess, "run", side_effect=[version, FileNotFoundError()]):
                self.assertEqual(MODULE.main(), 1)

    def test_missing_selected_interpreter_is_reported(self):
        with patch("sys.argv", ["install_local.py", "--python", "missing-python", "--skip-install"]):
            with patch.object(MODULE.subprocess, "run", side_effect=FileNotFoundError()):
                self.assertEqual(MODULE.main(), 1)


if __name__ == "__main__":
    unittest.main()
