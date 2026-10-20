"""Resource-boundary tests for isolated worker subprocesses."""

import sys
import unittest

from core.subprocess_executor import SubprocessExecutor


class SubprocessExecutorTests(unittest.TestCase):
    def test_reads_valid_json_object(self):
        executor = SubprocessExecutor(timeout_seconds=2, max_output_bytes=1024)
        result = executor.execute(
            [sys.executable, "-c", "import json,sys; print(json.dumps(json.load(sys.stdin)))"],
            {"safe": True},
        )
        self.assertEqual(result, {"safe": True})

    def test_rejects_stdout_overflow_while_streaming(self):
        executor = SubprocessExecutor(timeout_seconds=3, max_output_bytes=1024)
        with self.assertRaisesRegex(ValueError, "output exceeded"):
            executor.execute(
                [sys.executable, "-c", "import sys; sys.stdout.write('x' * 5000000)"],
                {},
            )

    def test_rejects_stderr_overflow_while_streaming(self):
        executor = SubprocessExecutor(timeout_seconds=3, max_output_bytes=1024)
        with self.assertRaisesRegex(ValueError, "output exceeded"):
            executor.execute(
                [sys.executable, "-c", "import sys; sys.stderr.write('x' * 5000000)"],
                {},
            )

    def test_times_out_and_terminates_worker(self):
        executor = SubprocessExecutor(timeout_seconds=0.1, max_output_bytes=1024)
        with self.assertRaisesRegex(TimeoutError, "timed out"):
            executor.execute([sys.executable, "-c", "import time; time.sleep(5)"], {})

    def test_rejects_non_object_json(self):
        executor = SubprocessExecutor(timeout_seconds=2, max_output_bytes=1024)
        with self.assertRaisesRegex(ValueError, "must be an object"):
            executor.execute([sys.executable, "-c", "print('[]')"], {})

    def test_rejects_invalid_timeout_override(self):
        executor = SubprocessExecutor()
        for timeout in (0, -1, True, "1"):
            with self.subTest(timeout=timeout), self.assertRaises(ValueError):
                executor.execute([sys.executable, "-c", "print('{}')"], {}, timeout_seconds=timeout)

    def test_rejects_invalid_output_limit_override(self):
        executor = SubprocessExecutor()
        for limit in (0, -1, True, 1.5):
            with self.subTest(limit=limit), self.assertRaises(ValueError):
                executor.execute([sys.executable, "-c", "print('{}')"], {}, max_output_bytes=limit)

    def test_rejects_oversized_input_before_starting_worker(self):
        executor = SubprocessExecutor()
        with self.assertRaisesRegex(ValueError, "input exceeded"):
            executor.execute([sys.executable, "-c", "print('{}')"], {"payload": "x" * 1_100_000})

    def test_worker_start_failure_is_sanitized(self):
        executor = SubprocessExecutor()
        with self.assertRaisesRegex(RuntimeError, "could not be started"):
            executor.execute(["/definitely/not/a/real/yuki-worker"], {})


if __name__ == "__main__":
    unittest.main()
