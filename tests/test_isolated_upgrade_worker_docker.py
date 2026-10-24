import json
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from automation.authorized_read_tools import ToolAuthorizationError
from automation.isolated_upgrade_worker_docker import DockerIsolatedTestRunner


IMAGE = "docker.io/library/python:3.11-slim@sha256:" + "a" * 64


class DockerIsolatedTestRunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.runner = DockerIsolatedTestRunner(
            self.root,
            image=IMAGE,
            trusted_principal=lambda principal: True,
            approve_run=lambda actor, run_id, digest: True,
            audit=lambda event: None,
            allowed_test_modules=("tests.test_sample",),
        )

    def test_rootless_docker_security_option_is_required(self):
        result = type("Result", (), {
            "returncode": 0,
            "stdout": json.dumps(["name=seccomp,profile=default", "name=rootless"]).encode(),
            "stderr": b"",
        })()
        with patch("automation.isolated_upgrade_worker_docker.subprocess.run", return_value=result):
            self.runner._verify_rootless_runtime("/usr/bin/docker")

    def test_rootful_docker_is_rejected(self):
        result = type("Result", (), {
            "returncode": 0,
            "stdout": json.dumps(["name=seccomp,profile=default"]).encode(),
            "stderr": b"",
        })()
        with patch("automation.isolated_upgrade_worker_docker.subprocess.run", return_value=result):
            with self.assertRaisesRegex(ToolAuthorizationError, "requires rootless Docker"):
                self.runner._verify_rootless_runtime("/usr/bin/docker")

    def test_docker_command_preserves_isolation_contract(self):
        command = self.runner._build_container_command(
            binary="/usr/bin/docker",
            cidfile=self.root / "worker.cid",
            artifact_path=self.root / "workspace.tar",
            artifact_digest="b" * 64,
            test_module="tests.test_sample",
        )
        self.assertEqual(command[0], "/usr/bin/docker")
        self.assertNotIn("--userns=keep-id", command)
        self.assertIn("--network=none", command)
        self.assertIn("--read-only", command)
        self.assertIn("--cap-drop=ALL", command)
        self.assertIn("--security-opt=no-new-privileges", command)
        self.assertIn("--pids-limit", command)
        self.assertIn("--memory", command)
        self.assertIn("--cpus", command)
        tmpfs = command[command.index("--tmpfs") + 1]
        self.assertIn("uid=65532", tmpfs)
        self.assertIn("gid=65532", tmpfs)
        self.assertIn("mode=700", tmpfs)
        mount = command[command.index("--mount") + 1]
        self.assertIn("dst=/tmp,readonly", mount)
        self.assertIn("--user=65532:65532", command)

    def test_runtime_environment_preserves_rootless_docker_socket_context(self):
        with patch.dict("os.environ", {
            "PATH": "/usr/bin", "HOME": "/home/worker",
            "DOCKER_HOST": "unix:///run/user/1000/docker.sock",
            "DOCKER_CONTEXT": "rootless", "AWS_SECRET_ACCESS_KEY": "must-not-forward",
        }, clear=True):
            env = self.runner._runtime_env()
        self.assertEqual(env["DOCKER_HOST"], "unix:///run/user/1000/docker.sock")
        self.assertEqual(env["DOCKER_CONTEXT"], "rootless")
        self.assertNotIn("AWS_SECRET_ACCESS_KEY", env)

    def test_cleanup_verifies_container_is_gone(self):
        cidfile = self.root / "worker.cid"
        cidfile.write_text("a" * 64, encoding="utf-8")
        absent = type("Result", (), {"returncode": 1, "stdout": b"", "stderr": b""})()
        with patch("automation.isolated_upgrade_worker_docker.subprocess.run", return_value=absent) as run:
            self.runner._ensure_container_removed("/usr/bin/docker", cidfile)
        self.assertEqual(run.call_count, 1)
        self.assertEqual(run.call_args.args[0][1:3], ["container", "ls"])

    def test_cleanup_fails_closed_when_daemon_cannot_verify_state(self):
        cidfile = self.root / "worker.cid"
        cidfile.write_text("a" * 64, encoding="utf-8")
        unavailable = type("Result", (), {"returncode": 2, "stdout": b"", "stderr": b"daemon unavailable"})()
        with patch("automation.isolated_upgrade_worker_docker.subprocess.run", return_value=unavailable):
            with self.assertRaisesRegex(RuntimeError, "unable to verify worker container state"):
                self.runner._ensure_container_removed("/usr/bin/docker", cidfile)

    def test_image_must_be_digest_pinned(self):
        with self.assertRaises(ValueError):
            DockerIsolatedTestRunner(
                self.root,
                image="python:3.11-slim",
                trusted_principal=lambda principal: True,
                approve_run=lambda actor, run_id, digest: True,
                audit=lambda event: None,
                allowed_test_modules=("tests.test_sample",),
            )


if __name__ == "__main__":
    unittest.main()
