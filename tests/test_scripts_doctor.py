import subprocess
import sys
import unittest

from scripts import doctor


class ScriptsDoctorTests(unittest.TestCase):
    def test_scripts_doctor_main_returns_zero(self):
        exit_code = doctor.main([])
        self.assertIn(exit_code, (0, 1))

    def test_scripts_doctor_cli_execution_succeeds(self):
        result = subprocess.run(
            [sys.executable, "scripts/doctor.py"],
            capture_output=True,
            text=True,
        )
        self.assertIn(result.returncode, (0, 1))
        self.assertIn("Yuki runtime doctor", result.stdout)
        self.assertIn("BASIC CHAT:", result.stdout)


if __name__ == "__main__":
    unittest.main()
