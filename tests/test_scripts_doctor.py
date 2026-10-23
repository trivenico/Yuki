import subprocess
import sys
import unittest

from scripts import doctor


class ScriptsDoctorTests(unittest.TestCase):
    def test_scripts_doctor_main_returns_zero(self):
        exit_code = doctor.main()
        self.assertEqual(exit_code, 0)

    def test_scripts_doctor_cli_execution_succeeds(self):
        result = subprocess.run(
            [sys.executable, "scripts/doctor.py"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("PASS: Python version, project files, and core imports look ready.", result.stdout)


if __name__ == "__main__":
    unittest.main()
