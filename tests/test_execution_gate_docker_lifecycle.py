import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from core.execution_gate_docker import build_gate_container_create_argv
from core.execution_gate_docker_lifecycle import (
    CreatedGateContainer, DockerGateContainerClient,
    ExecutionGateDockerLifecycleError,
)
from core.sandbox_policy import SandboxPolicy


CONTAINER_ID = "a" * 64
CONTAINER_NAME = "yuki-" + "b" * 32
IMAGE = "python@sha256:" + "c" * 64


class DockerGateContainerClientTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.docker = root / "docker"
        self.docker.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        self.docker.chmod(0o700)
        self.workspace = root / "workspace"
        self.workspace.mkdir()
        self.gate_dir = root / "gate"
        self.gate_dir.mkdir(mode=0o700)
        self.calls = []
        self.responses = []

        def runner(argv, **kwargs):
            self.calls.append((argv, kwargs))
            response = self.responses.pop(0)
            if isinstance(response, BaseException):
                raise response
            return response

        self.client = DockerGateContainerClient(
            docker_binary=str(self.docker), docker_host="unix:///run/user/1000/docker.sock",
            runner=runner,
        )
        self.labels = {
            "yuki.managed_by": "yuki", "yuki.task_id": "task-1",
            "yuki.worker_id": "worker-1", "yuki.owner_sha256": "d" * 64,
            "yuki.profile_id": "python-review",
            "yuki.image_digest": "sha256:" + "c" * 64,
        }
        self.argv = build_gate_container_create_argv(
            docker_binary=str(self.docker), policy=SandboxPolicy().validate(),
            image=IMAGE, workspace=str(self.workspace),
            gate_arguments=["--host-uid", "1000", "--task-id", "task-1",
                            "--worker-id", "worker-1", "--spec-id", "python-review",
                            "--image", IMAGE],
            private_gate_directory=str(self.gate_dir), container_name=CONTAINER_NAME,
            apparmor_profile="docker-default", container_labels=self.labels,
        )

    @staticmethod
    def result(code=0, stdout=b"", stderr=b""):
        return SimpleNamespace(returncode=code, stdout=stdout, stderr=stderr)

    def test_create_returns_full_daemon_id_then_start_uses_that_id(self):
        self.responses.extend([
            self.result(stdout=(CONTAINER_ID + "\n").encode()),
            self.result(stdout=(CONTAINER_ID + "\n").encode()),
        ])
        container = self.client.create(argv=self.argv, container_name=CONTAINER_NAME)
        self.assertEqual(container, CreatedGateContainer(CONTAINER_NAME, CONTAINER_ID))
        self.client.start(container)
        self.assertEqual(self.calls[0][0], self.argv)
        self.assertEqual(self.calls[1][0], [str(self.docker.resolve()), "start", CONTAINER_ID])
        self.assertEqual(self.calls[0][1]["env"]["DOCKER_HOST"], "unix:///run/user/1000/docker.sock")

    def test_invalid_create_id_triggers_remove_and_fails_closed(self):
        self.responses.extend([
            self.result(stdout=b"short-id\n"),
            self.result(),
        ])
        with self.assertRaisesRegex(ExecutionGateDockerLifecycleError, "full daemon-assigned"):
            self.client.create(argv=self.argv, container_name=CONTAINER_NAME)
        self.assertEqual(self.calls[1][0], [str(self.docker.resolve()), "rm", "-f", CONTAINER_NAME])

    def test_create_failure_attempts_cleanup(self):
        self.responses.extend([
            self.result(code=1, stderr=b"daemon error"),
            self.result(),
        ])
        with self.assertRaisesRegex(ExecutionGateDockerLifecycleError, "refused to create"):
            self.client.create(argv=self.argv, container_name=CONTAINER_NAME)
        self.assertEqual(self.calls[1][0][-3:], ["rm", "-f", CONTAINER_NAME])

    def test_start_failure_removes_container(self):
        self.responses.extend([
            self.result(stdout=CONTAINER_ID.encode()),
            self.result(code=1, stderr=b"start failed"),
            self.result(),
        ])
        container = self.client.create(argv=self.argv, container_name=CONTAINER_NAME)
        with self.assertRaisesRegex(ExecutionGateDockerLifecycleError, "failed to start"):
            self.client.start(container)
        self.assertEqual(self.calls[2][0][-3:], ["rm", "-f", CONTAINER_NAME])


    def test_create_timeout_reconciles_possible_container_by_unique_name(self):
        self.responses.extend([
            subprocess.TimeoutExpired(cmd="docker create", timeout=10),
            self.result(),
        ])
        with self.assertRaisesRegex(ExecutionGateDockerLifecycleError, "could not complete"):
            self.client.create(argv=self.argv, container_name=CONTAINER_NAME)
        self.assertEqual(self.calls[1][0], [str(self.docker.resolve()), "rm", "-f", CONTAINER_NAME])

    def test_remove_only_accepts_success_or_explicit_absence(self):
        self.responses.extend([
            self.result(code=1, stderr=b"No such container: " + CONTAINER_NAME.encode()),
            self.result(code=1, stderr=b"permission denied"),
        ])
        self.assertTrue(self.client.remove(container_name=CONTAINER_NAME))
        self.assertFalse(self.client.remove(container_name=CONTAINER_NAME))

    def test_rejects_non_gate_create_argv_and_invalid_container_identity(self):
        with self.assertRaisesRegex(ValueError, "fixed-entrypoint"):
            self.client.create(argv=[str(self.docker), "create", IMAGE, "sh"], container_name=CONTAINER_NAME)
        with self.assertRaises(ValueError):
            self.client.start(CreatedGateContainer(CONTAINER_NAME, "short"))


if __name__ == "__main__":
    unittest.main()
