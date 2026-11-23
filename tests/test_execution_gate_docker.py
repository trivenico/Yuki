import tempfile
import unittest
from pathlib import Path

from core.execution_gate_docker import (
    GATE_ENTRYPOINT,
    GATE_SOCKET_DIRECTORY,
    build_gate_container_create_argv,
)
from core.sandbox_policy import SandboxPolicy


IMAGE = "python@sha256:" + "a" * 64


class GateDockerCreateArgvTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.docker_binary = self.root / "docker"
        self.docker_binary.write_text("#!/bin/sh\\nexit 0\\n", encoding="utf-8")
        self.docker_binary.chmod(0o700)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.private = self.root / "gate"
        self.private.mkdir(mode=0o700)
        self.labels = {
            "yuki.managed_by": "yuki",
            "yuki.task_id": "task-123",
            "yuki.worker_id": "worker-456",
            "yuki.owner_sha256": "b" * 64,
            "yuki.profile_id": "python-review",
            "yuki.image_digest": "sha256:" + "a" * 64,
        }
        self.arguments = [
            "--host-uid", "1000",
            "--task-id", "task-123",
            "--worker-id", "worker-456",
            "--spec-id", "python-review",
            "--image", IMAGE,
            "--timeout", "30",
        ]

    def build(self, **overrides):
        values = {
            "docker_binary": str(self.docker_binary),
            "policy": SandboxPolicy().validate(),
            "image": IMAGE,
            "workspace": str(self.workspace),
            "gate_arguments": self.arguments,
            "private_gate_directory": str(self.private),
            "container_name": "yuki-" + "c" * 32,
            "apparmor_profile": "docker-default",
            "container_labels": self.labels,
        }
        values.update(overrides)
        return build_gate_container_create_argv(**values)

    def test_creates_container_with_gate_as_pid_one_and_no_task_command(self):
        argv = self.build()
        self.assertEqual(argv[0:2], ["/usr/bin/docker", "create"])
        self.assertNotIn("--init", argv)
        self.assertIn("--entrypoint", argv)
        self.assertEqual(argv[argv.index("--entrypoint") + 1], GATE_ENTRYPOINT)
        mount = argv[argv.index("--mount") + 1]
        self.assertEqual(
            mount,
            f"type=bind,src={self.private.resolve()},dst={GATE_SOCKET_DIRECTORY},rw",
        )
        image_index = argv.index(IMAGE)
        self.assertEqual(argv[image_index + 1:], self.arguments)
        self.assertNotIn("python", argv[image_index + 1:])

    def test_preserves_restrictive_policy_and_yuki_labels(self):
        argv = self.build()
        self.assertIn("--read-only", argv)
        self.assertIn("--cap-drop", argv)
        self.assertIn("ALL", argv)
        self.assertIn("--network", argv)
        self.assertIn("none", argv)
        for key, value in self.labels.items():
            self.assertIn(f"{key}={value}", argv)

    def test_rejects_symlink_private_directory(self):
        link = self.root / "gate-link"
        link.symlink_to(self.private, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "non-symlink"):
            self.build(private_gate_directory=str(link))

    def test_rejects_untrusted_container_name(self):
        with self.assertRaisesRegex(ValueError, "generated Yuki"):
            self.build(container_name="task-controlled-name")

    def test_rejects_incomplete_ownership_labels(self):
        with self.assertRaisesRegex(ValueError, "complete validated"):
            self.build(container_labels={"yuki.managed_by": "yuki"})

    def test_rejects_gate_argument_that_attempts_entrypoint_override(self):
        with self.assertRaisesRegex(ValueError, "cannot override"):
            self.build(gate_arguments=["--entrypoint", "/bin/sh"])


if __name__ == "__main__":
    unittest.main()
