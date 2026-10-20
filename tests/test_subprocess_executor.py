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


    def test_timeout_kills_descendant_that_ignores_sigterm(self):
        import os
        import tempfile
        import time

        if os.name != "posix" or not os.path.isdir("/proc"):
            self.skipTest("requires POSIX process groups and Linux /proc")
        with tempfile.TemporaryDirectory() as temp_dir:
            pid_file = os.path.join(temp_dir, "child.pid")
            child_code = (
                "import signal,time; "
                "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
                "time.sleep(30)"
            )
            parent_code = (
                "import subprocess,sys,time; "
                f"child=subprocess.Popen([sys.executable, '-c', {child_code!r}]); "
                f"open({pid_file!r}, 'w').write(str(child.pid)); "
                "time.sleep(30)"
            )
            executor = SubprocessExecutor(timeout_seconds=0.4, max_output_bytes=1024)
            with self.assertRaisesRegex(TimeoutError, "timed out"):
                executor.execute([sys.executable, "-c", parent_code], {})
            self.assertTrue(os.path.exists(pid_file))
            child_pid = int(open(pid_file, encoding="utf-8").read())
            # Killed orphaned children can briefly remain zombies until init reaps
            # them; they must no longer be executing.
            for _ in range(50):
                proc_stat = f"/proc/{child_pid}/stat"
                if not os.path.exists(proc_stat):
                    break
                with open(proc_stat, encoding="utf-8") as handle:
                    state = handle.read().split()[2]
                if state == "Z":
                    break
                time.sleep(0.02)
            else:
                self.fail("worker descendant survived timeout cleanup")

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
