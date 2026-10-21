import io
import os
import subprocess
import unittest
from unittest.mock import Mock, patch

from core.sandbox_executor import SandboxExecutionError, execute_docker_sandbox
from core.sandbox_policy import SandboxPolicy


class SandboxExecutorTests(unittest.TestCase):
    def setUp(self):
        self.policy = SandboxPolicy(timeout_seconds=2, max_output_bytes=16)
        self.argv = ["docker", "run", "--rm", "--network", "none", "python:3.12", "python", "-c", "pass"]

    @patch("core.sandbox_executor.build_docker_argv")
    @patch("core.sandbox_executor.subprocess.run")
    @patch("core.sandbox_executor.subprocess.Popen")
    def test_returns_bounded_output_and_cleans_container(self, popen, run, build):
        build.return_value = list(self.argv)
        process = Mock()
        process.stdout = io.BytesIO(b"x" * 100)
        process.stderr = io.BytesIO(b"")
        process.wait.return_value = 0
        process.poll.return_value = 0
        process.returncode = 0
        popen.return_value = process
        run.return_value = Mock(returncode=0)

        result = execute_docker_sandbox(
            self.policy, image="python:3.12", workspace="/tmp", command=["python", "-c", "pass"]
        )

        self.assertEqual(len(result.stdout.encode()), 16)
        self.assertTrue(result.output_truncated)
        self.assertFalse(result.timed_out)
        self.assertTrue(result.container_cleanup_attempted)
        self.assertEqual(run.call_args.args[0][:3], ["docker", "rm", "-f"])
        self.assertFalse(popen.call_args.kwargs["shell"])

    @patch("core.sandbox_executor.os.killpg")
    @patch("core.sandbox_executor.build_docker_argv")
    @patch("core.sandbox_executor.subprocess.run")
    @patch("core.sandbox_executor.subprocess.Popen")
    def test_timeout_terminates_process_and_cleans_container(self, popen, run, build, killpg):
        build.return_value = list(self.argv)
        process = Mock()
        process.stdout = io.BytesIO(b"partial")
        process.stderr = io.BytesIO(b"")
        process.wait.side_effect = [subprocess.TimeoutExpired("docker", 2), -9]
        process.poll.return_value = -9
        process.returncode = -9
        popen.return_value = process
        run.return_value = Mock(returncode=0)

        result = execute_docker_sandbox(
            self.policy, image="python:3.12", workspace="/tmp", command=["python", "-c", "pass"]
        )

        self.assertTrue(result.timed_out)
        if os.name == "posix":
            killpg.assert_called_once()
        else:
            process.kill.assert_called_once()
        self.assertTrue(result.container_cleanup_attempted)

    @patch("core.sandbox_executor.build_docker_argv")
    @patch("core.sandbox_executor.subprocess.run")
    @patch("core.sandbox_executor.subprocess.Popen")
    def test_cleanup_failure_is_not_reported_as_success(self, popen, run, build):
        build.return_value = list(self.argv)
        process = Mock()
        process.stdout = io.BytesIO(b"")
        process.stderr = io.BytesIO(b"")
        process.wait.return_value = 0
        process.poll.return_value = 0
        process.returncode = 0
        popen.return_value = process
        run.return_value = Mock(returncode=1)

        with self.assertRaisesRegex(SandboxExecutionError, "cleanup could not be confirmed"):
            execute_docker_sandbox(
                self.policy, image="python:3.12", workspace="/tmp", command=["python", "-c", "pass"]
            )

    @patch("core.sandbox_executor.build_docker_argv")
    def test_invalid_policy_is_rejected_before_process_creation(self, build):
        from core.sandbox_policy import SandboxPolicyError
        build.side_effect = SandboxPolicyError("invalid")
        with self.assertRaises(SandboxExecutionError):
            execute_docker_sandbox(
                self.policy, image="python:3.12", workspace="/tmp", command=["python", "-c", "pass"]
            )


if __name__ == "__main__":
    unittest.main()
