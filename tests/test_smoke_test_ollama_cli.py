"""CLI regression tests for the opt-in Ollama smoke test."""
import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "smoke_test_ollama.py"


class OllamaSmokeTestCliTests(unittest.TestCase):
    def run_script(self, *args, timeout_value="45"):
        env = os.environ.copy()
        env["YUKI_PROVIDER_TIMEOUT_SECONDS"] = timeout_value
        # Invalid-timeout cases exit before a network request; help must never connect.
        env["YUKI_OLLAMA_BASE_URL"] = "http://127.0.0.1:11434"
        return subprocess.run(
            [sys.executable, str(SCRIPT), *args],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )

    def test_invalid_environment_timeout_is_a_cli_error_not_traceback(self):
        result = self.run_script(timeout_value="not-a-number")
        self.assertEqual(result.returncode, 2)
        self.assertIn("invalid float value", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_explicit_timeout_overrides_invalid_environment_default(self):
        result = self.run_script("--timeout", "0", timeout_value="not-a-number")
        # The explicit CLI value is parsed; provider validation rejects 0 before networking.
        self.assertEqual(result.returncode, 2)
        self.assertIn("Timeout must be between 1 and 120 seconds", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_help_does_not_attempt_network_request(self):
        result = self.run_script("--help", timeout_value="not-a-number")
        self.assertEqual(result.returncode, 0)
        self.assertIn("--timeout", result.stdout)


if __name__ == "__main__":
    unittest.main()
