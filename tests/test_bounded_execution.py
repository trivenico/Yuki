import os
import sys
import unittest

from core.bounded_execution import ExecutionError, run_bounded


class BoundedExecutionTests(unittest.TestCase):
    def test_captures_output_and_exit_code(self):
        result = run_bounded(
            [sys.executable, "-c", "print('ok')"],
            timeout_seconds=5, max_output_bytes=1024, env=os.environ.copy(),
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn(b"ok", result.stdout)
        self.assertFalse(result.timed_out)

    def test_output_retention_is_bounded(self):
        result = run_bounded(
            [sys.executable, "-c", "import sys; sys.stdout.write('x'*200000)"],
            timeout_seconds=5, max_output_bytes=1024, env=os.environ.copy(),
        )
        self.assertLessEqual(len(result.stdout), 1024)
        self.assertTrue(result.output_truncated)

    def test_timeout_is_reported(self):
        result = run_bounded(
            [sys.executable, "-c", "import time; time.sleep(10)"],
            timeout_seconds=0.2, max_output_bytes=1024, env=os.environ.copy(),
        )
        self.assertTrue(result.timed_out)
        self.assertNotEqual(result.returncode, 0)

    def test_shell_string_is_rejected(self):
        with self.assertRaises(ExecutionError):
            run_bounded("echo unsafe", timeout_seconds=1, max_output_bytes=10)

    def test_invalid_limits_are_rejected(self):
        for timeout, output_limit in ((0, 1), (3601, 1), (1, 0), (1, True)):
            with self.subTest(timeout=timeout, output_limit=output_limit):
                with self.assertRaises(ExecutionError):
                    run_bounded([sys.executable, "--version"],
                                timeout_seconds=timeout,
                                max_output_bytes=output_limit)

    def test_nul_in_argument_is_rejected(self):
        with self.assertRaises(ExecutionError):
            run_bounded([sys.executable, "bad\x00arg"],
                        timeout_seconds=1, max_output_bytes=10)


if __name__ == "__main__":
    unittest.main()
