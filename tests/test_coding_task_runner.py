import io
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from core.coding_task_runner import SandboxExecutionError, run_sandboxed_task
from core.sandbox_policy import SandboxPolicy


class FakeProcess:
    def __init__(self, output=b"ok\n", returncode=0):
        self.stdout = io.BytesIO(output)
        self.returncode = returncode
        self.killed = False

    def wait(self, timeout=None):
        if self.killed:
            return -9
        return self.returncode

    def kill(self):
        self.killed = True


class CodingTaskRunnerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.policy = SandboxPolicy(max_output_bytes=8, timeout_seconds=2)

    @patch("core.coding_task_runner._kill_container")
    @patch("core.coding_task_runner.subprocess.Popen")
    @patch("core.coding_task_runner.build_docker_argv", return_value=["docker", "run", "python:3.12", "python", "-V"])
    def test_requires_approval_before_launch(self, build, popen, _kill):
        with self.assertRaises(SandboxExecutionError):
            run_sandboxed_task(self.policy, image="python:3.12", workspace=self.tmp.name,
                               command=["python", "-V"], approve=lambda: False)
        popen.assert_not_called()

    @patch("core.coding_task_runner._kill_container")
    @patch("core.coding_task_runner.subprocess.Popen")
    @patch("core.coding_task_runner.build_docker_argv", return_value=["docker", "run", "python:3.12", "python", "-V"])
    def test_approval_callback_exception_fails_closed(self, build, popen, _kill):
        def broken():
            raise RuntimeError("approval service unavailable")
        with self.assertRaises(SandboxExecutionError):
            run_sandboxed_task(self.policy, image="python:3.12", workspace=self.tmp.name,
                               command=["python", "-V"], approve=broken)
        popen.assert_not_called()

    @patch("core.coding_task_runner._kill_container")
    @patch("core.coding_task_runner.subprocess.Popen")
    @patch("core.coding_task_runner.build_docker_argv", return_value=["docker", "run", "python:3.12", "python", "-V"])
    def test_success_is_argv_only_and_output_is_bounded(self, build, popen, _kill):
        proc = FakeProcess(b"123456789012")
        popen.return_value = proc
        result = run_sandboxed_task(self.policy, image="python:3.12", workspace=self.tmp.name,
                                    command=["python", "-V"], approve=lambda: True)
        self.assertEqual(len(result.output.encode("utf-8")), 8)
        self.assertTrue(result.output_truncated)
        self.assertEqual(result.returncode, -9)
        args, kwargs = popen.call_args
        self.assertIsInstance(args[0], list)
        self.assertIs(kwargs["shell"], False)
        self.assertIn("--name", args[0])
        self.assertTrue(kwargs["env"])
        _kill.assert_called_once()

    @patch("core.coding_task_runner._kill_container")
    @patch("core.coding_task_runner.subprocess.Popen")
    @patch("core.coding_task_runner.build_docker_argv", return_value=["docker", "run", "python:3.12", "python", "-V"])
    def test_normal_output_returns_result(self, build, popen, _kill):
        popen.return_value = FakeProcess(b"ok\n", 0)
        result = run_sandboxed_task(self.policy, image="python:3.12", workspace=self.tmp.name,
                                    command=["python", "-V"], approve=lambda: True)
        self.assertEqual(result.output, "ok\n")
        self.assertEqual(result.returncode, 0)
        self.assertFalse(result.timed_out)
        self.assertFalse(result.output_truncated)
        _kill.assert_not_called()

    @patch("core.coding_task_runner._kill_container")
    @patch("core.coding_task_runner.subprocess.Popen")
    @patch("core.coding_task_runner.build_docker_argv", return_value=["docker", "run", "python:3.12", "python", "-V"])
    def test_timeout_kills_client_and_container(self, build, popen, kill_container):
        proc = FakeProcess()
        proc.wait = unittest.mock.Mock(side_effect=[subprocess.TimeoutExpired("docker", 2), -9])
        popen.return_value = proc
        result = run_sandboxed_task(self.policy, image="python:3.12", workspace=self.tmp.name,
                                    command=["python", "-V"], approve=lambda: True)
        self.assertTrue(result.timed_out)
        self.assertEqual(result.returncode, -9)
        kill_container.assert_called_once()

    def test_missing_approval_callback_is_rejected(self):
        with self.assertRaises(SandboxExecutionError):
            run_sandboxed_task(self.policy, image="python:3.12", workspace=self.tmp.name,
                               command=["python", "-V"], approve=None)


if __name__ == "__main__":
    unittest.main()
