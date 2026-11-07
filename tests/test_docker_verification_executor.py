import hashlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from automation.docker_verification_executor import (
    DockerVerificationError,
    DockerVerificationExecutor,
    SandboxCommand,
)
from automation.repository_snapshot import RepositorySnapshotBuilder


class FakeProcess:
    def __init__(self, argv):
        self.argv = argv
        self.stdout = io.BytesIO(b"tests passed\n")
        self.stderr = io.BytesIO(b"")
        self.returncode = None
        self.killed = False

    def wait(self, timeout=None):
        self.returncode = 0
        return 0

    def kill(self):
        self.killed = True
        self.returncode = -9


class DockerVerificationExecutorTests(unittest.TestCase):
    def _executor(self, base):
        workspace = base / "workspace"
        workspace.mkdir()
        (workspace / "test_sample.py").write_text("def test_ok(): assert True\n")
        snapshot = RepositorySnapshotBuilder().build({
            "test_sample.py": (workspace / "test_sample.py").read_bytes()
        })
        executor = DockerVerificationExecutor(
            workspace, base / "receipts.sqlite3",
            image="python:3.12-slim@sha256:" + "a" * 64,
            expected_snapshot_sha256=snapshot.snapshot_sha256,
            commands={"unit-tests": SandboxCommand(
                "unit-tests", ("python", "-m", "pytest", "-q"), timeout_seconds=30
            )},
        )
        return executor, workspace

    def test_runs_pinned_command_with_hardened_docker_flags_and_receipt(self):
        with tempfile.TemporaryDirectory() as temp, patch(
            "automation.docker_verification_executor.shutil.which", return_value="/usr/bin/docker"
        ), patch(
            "automation.docker_verification_executor.subprocess.Popen",
            side_effect=lambda argv, **kwargs: FakeProcess(argv),
        ) as popen:
            executor, _ = self._executor(Path(temp))
            receipt = executor.run("unit-tests", artifact_sha256="b" * 64)
            argv = popen.call_args.args[0]
            self.assertIn("--network=none", argv)
            self.assertIn("--read-only", argv)
            self.assertIn("--cap-drop=ALL", argv)
            self.assertIn("--security-opt=no-new-privileges:true", argv)
            self.assertIn("--security-opt=seccomp=default", argv)
            self.assertIn("--pull=never", argv)
            self.assertEqual(receipt.status, "passed")
            stored = executor.get(receipt.execution_id)
            self.assertEqual(stored["artifact_sha256"], "b" * 64)
            self.assertEqual(stored["argv_json"], '["python", "-m", "pytest", "-q"]')
            reference = "yuki-verification-execution://" + receipt.execution_id
            self.assertTrue(executor.validate_evidence_reference(
                reference, artifact_sha256="b" * 64, check_name="tests"
            ))
            self.assertFalse(executor.validate_evidence_reference(
                reference, artifact_sha256="c" * 64, check_name="tests"
            ))
            self.assertFalse(executor.validate_evidence_reference(
                reference, artifact_sha256="b" * 64, check_name="security"
            ))

    def test_rejects_workspace_tampering_before_launch(self):
        with tempfile.TemporaryDirectory() as temp, patch(
            "automation.docker_verification_executor.shutil.which", return_value="/usr/bin/docker"
        ), patch(
            "automation.docker_verification_executor.subprocess.Popen"
        ) as popen:
            executor, workspace = self._executor(Path(temp))
            (workspace / "test_sample.py").write_text("tampered\n")
            with self.assertRaisesRegex(DockerVerificationError, "snapshot_digest_mismatch"):
                executor.run("unit-tests", artifact_sha256="b" * 64)
            popen.assert_not_called()

    def test_rejects_symlink_workspace_root(self):
        with tempfile.TemporaryDirectory() as temp, patch(
            "automation.docker_verification_executor.shutil.which", return_value="/usr/bin/docker"
        ):
            base = Path(temp)
            real = base / "real"
            real.mkdir()
            link = base / "link"
            link.symlink_to(real, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "real_directory"):
                DockerVerificationExecutor(
                    link, base / "receipts.sqlite3",
                    image="python:3.12-slim@sha256:" + "a" * 64,
                    expected_snapshot_sha256="a" * 64,
                    commands={"unit-tests": SandboxCommand("unit-tests", ("python", "-m", "pytest"))},
                )

    def test_rejects_mutable_image_tag(self):
        with tempfile.TemporaryDirectory() as temp, patch(
            "automation.docker_verification_executor.shutil.which", return_value="/usr/bin/docker"
        ):
            root = Path(temp) / "workspace"
            root.mkdir()
            with self.assertRaisesRegex(ValueError, "digest_pinned"):
                DockerVerificationExecutor(
                    root, Path(temp) / "receipts.sqlite3", image="python:latest",
                    expected_snapshot_sha256="a" * 64,
                    commands={"unit-tests": SandboxCommand("unit-tests", ("python", "-m", "pytest"))},
                )


if __name__ == "__main__":
    unittest.main()
