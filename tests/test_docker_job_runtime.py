import subprocess
import tempfile
import unittest
from pathlib import Path

from automation.docker_job_runtime import (
    DockerJobHandle,
    DockerJobRuntime,
    DockerJobRuntimeError,
)


class FakeDockerRunner:
    def __init__(self):
        self.calls = []
        self.running = "false"
        self.fail_inspect = False

    def __call__(self, argv, **kwargs):
        self.calls.append((list(argv), kwargs))
        if argv[1:2] == ["inspect"]:
            if self.fail_inspect:
                return subprocess.CompletedProcess(argv, 1, b"", b"")
            return subprocess.CompletedProcess(argv, 0, self.running.encode(), b"")
        if argv[1:2] == ["run"]:
            return subprocess.CompletedProcess(argv, 0, b"container-id\n", b"")
        return subprocess.CompletedProcess(argv, 0, b"", b"")


class DockerJobRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.output = self.root / "output"
        self.output.mkdir(mode=0o700)
        self.runner = FakeDockerRunner()
        self.runtime = DockerJobRuntime(
            workspace=self.workspace,
            image="registry.example/yuki-runner@sha256:" + "a" * 64,
            command=("python", "-m", "yuki_job"),
            docker_binary="docker",
            runner=self.runner,
        )
        self.job_id = "job_" + "b" * 32

    def test_launch_uses_detached_named_container_and_isolated_mounts(self):
        handle = self.runtime.launch(self.job_id, str(self.output))
        self.assertEqual(handle, DockerJobHandle(self.job_id, "yuki-job-" + "b" * 32))
        argv = self.runner.calls[0][0]
        self.assertEqual(argv[0:2], ["docker", "run"])
        self.assertIn("--detach", argv)
        self.assertIn("--name", argv)
        self.assertNotIn("--rm", argv)
        mounts = [argv[i + 1] for i, item in enumerate(argv[:-1]) if item == "--mount"]
        self.assertEqual(len(mounts), 2)
        self.assertTrue(mounts[0].endswith(",ro"))
        self.assertTrue(mounts[1].endswith(",rw"))
        self.assertIn("--network", argv)
        self.assertEqual(argv[argv.index("--network") + 1], "none")

    def test_freeze_requires_inspect_to_confirm_stopped_and_removes_container(self):
        handle = self.runtime.launch(self.job_id, str(self.output))
        self.assertTrue(self.runtime.freeze(handle))
        commands = [call[0][1] for call in self.runner.calls]
        self.assertIn("stop", commands)
        self.assertIn("inspect", commands)
        self.assertIn("rm", commands)

    def test_freeze_fails_closed_if_inspect_reports_running(self):
        handle = self.runtime.launch(self.job_id, str(self.output))
        self.runner.running = "true"
        with self.assertRaises(DockerJobRuntimeError):
            self.runtime.freeze(handle)
        self.assertNotIn("rm", [call[0][1] for call in self.runner.calls])

    def test_freeze_fails_closed_if_inspect_fails(self):
        handle = self.runtime.launch(self.job_id, str(self.output))
        self.runner.fail_inspect = True
        with self.assertRaises(DockerJobRuntimeError):
            self.runtime.freeze(handle)

    def test_rejects_unpinned_image_and_forged_handle(self):
        with self.assertRaises(ValueError):
            DockerJobRuntime(
                workspace=self.workspace, image="python:3.12",
                command=("python", "--version"), runner=self.runner,
            )
        with self.assertRaises(DockerJobRuntimeError):
            self.runtime.freeze(DockerJobHandle(self.job_id, "other-container"))

    def test_runner_is_always_shell_free_and_bounded(self):
        self.runtime.launch(self.job_id, str(self.output))
        _argv, kwargs = self.runner.calls[0]
        self.assertIs(kwargs["shell"], False)
        self.assertEqual(kwargs["timeout"], 15)


if __name__ == "__main__":
    unittest.main()
