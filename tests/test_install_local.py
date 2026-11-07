"""Tests for isolated local installer interpreter and environment selection."""
import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "install_local.py"
SPEC = importlib.util.spec_from_file_location("yuki_install_local", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class InstallLocalTests(unittest.TestCase):
    def test_uses_requested_python_and_custom_environment_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "portable" / "venv"
            version = subprocess.CompletedProcess([], 0, stdout="3.12\n", stderr="")
            with patch("sys.argv", ["install_local.py", "--python", "python3.12", "--venv-dir", str(target), "--skip-install"]):
                with patch.object(MODULE.subprocess, "run", side_effect=[version, None]) as run:
                    self.assertEqual(MODULE.main(), 0)
            self.assertEqual(run.call_count, 2)
            self.assertEqual(run.call_args_list[1].args[0], ["python3.12", "-m", "venv", str(target.resolve())])

    def test_environment_directory_can_come_from_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "env"
            version = subprocess.CompletedProcess([], 0, stdout="3.12\n", stderr="")
            with patch.dict(MODULE.os.environ, {"YUKI_VENV_DIR": str(target)}):
                with patch("sys.argv", ["install_local.py", "--skip-install"]):
                    with patch.object(MODULE.subprocess, "run", side_effect=[version, None]) as run:
                        self.assertEqual(MODULE.main(), 0)
            self.assertEqual(run.call_args_list[1].args[0][-1], str(target.resolve()))

    def test_rejects_selected_python_below_minimum_before_creating_venv(self):
        version = subprocess.CompletedProcess([], 0, stdout="3.10\n", stderr="")
        with patch("sys.argv", ["install_local.py", "--python", "python3.9", "--skip-install"]):
            with patch.object(MODULE.subprocess, "run", return_value=version) as run:
                self.assertEqual(MODULE.main(), 1)
        run.assert_called_once()

    def test_venv_creation_failure_is_reported(self):
        version = subprocess.CompletedProcess([], 0, stdout="3.11\n", stderr="")
        with patch("sys.argv", ["install_local.py", "--skip-install"]):
            with patch.object(MODULE.subprocess, "run", side_effect=[version, FileNotFoundError()]):
                self.assertEqual(MODULE.main(), 1)


if __name__ == "__main__":
    unittest.main()
