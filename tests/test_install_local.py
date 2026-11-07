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
        with patch("sys.argv", ["install_local.py", "--python", "python3.12", "--skip-install"]):
            with patch.object(MODULE.subprocess, "run") as run:
                self.assertEqual(MODULE.main(), 0)
        run.assert_called_once()
        self.assertEqual(run.call_args.args[0][:3], ["python3.12", "-m", "venv"])

    def test_venv_creation_failure_is_reported(self):
        with patch("sys.argv", ["install_local.py", "--python", "missing-python", "--skip-install"]):
            with patch.object(MODULE.subprocess, "run", side_effect=FileNotFoundError()):
                self.assertEqual(MODULE.main(), 1)


if __name__ == "__main__":
    unittest.main()
