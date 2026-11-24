import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.coding_task_runner import SandboxExecutionError, run_sandboxed_task
from core.sandbox_policy import SandboxPolicy


class DockerExecutablePathTests(unittest.TestCase):
    def test_rejects_relative_docker_executable_before_approval(self):
        with tempfile.TemporaryDirectory() as workspace:
            approve = unittest.mock.Mock(return_value=True)
            with self.assertRaises(SandboxExecutionError):
                run_sandboxed_task(
                    SandboxPolicy(), image="python@sha256:" + "a" * 64,
                    workspace=workspace, command=["python", "-V"],
                    approve=approve, docker_binary="docker",
                )
            approve.assert_not_called()

    @patch("core.coding_task_runner._kill_container", return_value=True)
    @patch("core.coding_task_runner.subprocess.Popen")
    @patch("core.coding_task_runner.build_docker_argv",
           return_value=["docker", "run", "python@sha256:" + "a" * 64, "python", "-V"])
    def test_host_process_uses_configured_absolute_docker_path(self, _build, popen, _kill):
        class Process:
            stdout = __import__("io").BytesIO(b"ok")
            def wait(self, timeout=None): return 0
        popen.return_value = Process()
        with tempfile.TemporaryDirectory() as workspace:
            run_sandboxed_task(
                SandboxPolicy(), image="python@sha256:" + "a" * 64,
                workspace=workspace, command=["python", "-V"],
                approve=lambda request: True, docker_binary="/opt/yuki/bin/docker",
            )
        self.assertEqual(popen.call_args.args[0][0], "/opt/yuki/bin/docker")
