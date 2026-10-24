import hashlib
from pathlib import Path
import tempfile
import tarfile
import unittest
from unittest.mock import patch

from automation.authorized_read_tools import ToolAuthorizationError, ToolInputError, ToolPrincipal
from automation.isolated_upgrade_worker import PodmanIsolatedTestRunner


IMAGE = "localhost/yuki-test-worker@sha256:" + "a" * 64


class PodmanIsolatedTestRunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "tests").mkdir()
        (self.root / "tests" / "__init__.py").write_text("", encoding="utf-8")
        self.source = self.root / "tests" / "test_sample.py"
        self.source.write_text("import unittest\nclass T(unittest.TestCase):\n def test_ok(self): self.assertTrue(True)\n", encoding="utf-8")
        self.principal = ToolPrincipal("alice", frozenset({"tests:execute"}))
        self.events = []
        self.approved = True
        self.runner = self.make_runner()

    def make_runner(self, **overrides):
        args = dict(
            workspace=self.root,
            image=IMAGE,
            trusted_principal=lambda p: p.actor_id == "alice",
            approve_run=lambda actor, run_id, digest: self.approved,
            audit=self.events.append,
            allowed_test_modules=("tests.test_sample",),
        )
        args.update(overrides)
        return PodmanIsolatedTestRunner(**args)

    def test_image_must_be_immutable_digest(self):
        for image in ("python:3.12", "localhost/yuki:latest", "", "repo@sha256:abc"):
            with self.subTest(image=image), self.assertRaises(ValueError):
                self.make_runner(image=image)

    def test_invalid_target_denied_before_runtime_lookup(self):
        with patch("automation.isolated_upgrade_worker.shutil.which") as which:
            with self.assertRaises(ToolAuthorizationError):
                self.runner.run(principal=self.principal, test_module="tests.other")
        which.assert_not_called()

    def test_denied_approval_never_invokes_runtime(self):
        self.approved = False
        with patch("automation.isolated_upgrade_worker.shutil.which") as which:
            with self.assertRaises(ToolAuthorizationError):
                self.runner.run(principal=self.principal, test_module="tests.test_sample")
        which.assert_not_called()
        self.assertEqual(self.events[-1]["outcome"], "denied")

    def test_missing_runtime_fails_closed_and_is_audited(self):
        with patch("automation.isolated_upgrade_worker.shutil.which", return_value=None):
            with self.assertRaisesRegex(ToolAuthorizationError, "Podman executable is unavailable"):
                self.runner.run(principal=self.principal, test_module="tests.test_sample")
        self.assertEqual(self.events[-1]["outcome"], "runtime_unavailable")

    def test_rootful_runtime_is_rejected(self):
        fake = type("FakeCompleted", (), {"returncode": 0, "stdout": b'{"host":{"security":{"rootless":false}}}', "stderr": b""})()
        with patch("automation.isolated_upgrade_worker.subprocess.run", return_value=fake):
            with self.assertRaisesRegex(ToolAuthorizationError, "requires rootless Podman"):
                self.runner._verify_rootless_runtime("/usr/bin/podman")

    def test_audit_failure_denies_worker_result(self):
        runner = self.make_runner(audit=lambda event: (_ for _ in ()).throw(OSError("audit down")))
        with self.assertRaisesRegex(RuntimeError, "audit unavailable"):
            runner._record("alice", "requested", "d" * 64, "tests.test_sample")

    def test_symlink_test_target_is_rejected(self):
        outside = self.root / "outside.py"
        outside.write_text("VALUE = 1\n", encoding="utf-8")
        link = self.root / "tests" / "test_link.py"
        try:
            link.symlink_to(outside)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks unavailable")
        runner = self.make_runner(allowed_test_modules=("tests.test_link",))
        with self.assertRaises((ToolAuthorizationError, ToolInputError)):
            runner.run(principal=self.principal, test_module="tests.test_link")


    def test_workspace_digest_changes_when_any_file_changes(self):
        before = self.runner._workspace_digest()
        (self.root / "README.md").write_text("different staged content", encoding="utf-8")
        after = self.runner._workspace_digest()
        self.assertNotEqual(before, after)

    def test_workspace_digest_rejects_symlink_directory(self):
        target = self.root / "outside"
        target.mkdir()
        link = self.root / "linked"
        try:
            link.symlink_to(target, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("directory symlinks unavailable")
        with self.assertRaises(ToolInputError):
            self.runner._workspace_digest()


    def test_snapshot_is_private_copy_and_original_is_unchanged(self):
        original = self.source.read_bytes()
        with tempfile.TemporaryDirectory() as tmp:
            snapshot = Path(tmp) / "workspace"
            self.runner._create_workspace_snapshot(snapshot)
            copied = snapshot / "tests" / "test_sample.py"
            self.assertEqual(copied.read_bytes(), original)
            self.assertNotEqual(copied.resolve(), self.source.resolve())
            self.assertEqual(copied.stat().st_mode & 0o222, 0)
            self.assertEqual(self.source.read_bytes(), original)
            for current, _dirs, _files in __import__("os").walk(snapshot, topdown=False):
                Path(current).chmod(0o700)

    def test_snapshot_rejects_symlink_and_does_not_copy_outside_file(self):
        outside = self.root.parent / (self.root.name + "-outside.py")
        outside.write_text("SECRET = True\\n", encoding="utf-8")
        link = self.root / "tests" / "test_escape.py"
        try:
            link.symlink_to(outside)
        except (OSError, NotImplementedError):
            outside.unlink(missing_ok=True)
            self.skipTest("symlinks unavailable")
        try:
            with tempfile.TemporaryDirectory() as tmp:
                snapshot = Path(tmp) / "workspace"
                with self.assertRaises(ToolInputError):
                    self.runner._create_workspace_snapshot(snapshot)
                self.assertFalse((snapshot / "tests" / "test_escape.py").exists())
        finally:
            outside.unlink(missing_ok=True)
            if (self.root / "tests").exists():
                (self.root / "tests").chmod(0o700)


    def test_trusted_harness_is_valid_python_and_uses_explicit_test_loader(self):
        harness = self.runner._trusted_harness()
        compile(harness, "<yuki-isolated-worker-harness>", "exec")
        self.assertIn("hashlib.sha256", harness)
        self.assertIn("archive=pathlib.Path('/tmp/workspace.tar')", harness)
        self.assertNotIn("/input/workspace.tar", harness)
        self.assertIn("unittest.defaultTestLoader.loadTestsFromName(target)", harness)
        self.assertNotIn("unittest.main(module=None", harness)

    def test_container_command_uses_readonly_artifact_and_bounded_tmpfs(self):
        command = self.runner._build_container_command(
            binary="/usr/bin/podman",
            cidfile=Path("/tmp/container.cid"),
            artifact_path=Path("/tmp/artifact/workspace.tar"),
            artifact_digest="a" * 64,
            test_module="tests.test_sample",
        )
        self.assertIn("--network=none", command)
        self.assertIn("--read-only", command)
        self.assertIn("--cap-drop=ALL", command)
        self.assertIn("--security-opt=no-new-privileges", command)
        self.assertIn("/work:rw,nosuid,nodev,size=100m", command)
        mount = next(arg for arg in command if arg.startswith("type=bind,src="))
        self.assertEqual(mount.replace("\\", "/"), "type=bind,src=/tmp/artifact,dst=/tmp,ro")
        self.assertIn("TMPDIR=/work", command)
        self.assertIn("TMP=/work", command)
        self.assertIn("TEMP=/work", command)
        self.assertIn("--pull=never", command)
        self.assertNotIn("--privileged", command)
        self.assertEqual(command.count("--network=none"), 1)

    def test_snapshot_and_artifact_exclude_common_credentials_and_runtime_state(self):
        secret_files = [
            self.root / ".env",
            self.root / ".env.production",
            self.root / "credentials.json",
            self.root / "private.key",
            self.root / ".aws" / "credentials",
            self.root / ".git" / "config",
            self.root / ".venv" / "pyvenv.cfg",
        ]
        for path in secret_files:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("do-not-package-this", encoding="utf-8")

        with tempfile.TemporaryDirectory() as tmp:
            snapshot = Path(tmp) / "snapshot"
            self.runner._create_workspace_snapshot(snapshot)
            artifact, _digest = self.runner._create_content_addressed_artifact(
                Path(tmp) / "workspace.tar", root=snapshot
            )
            with tarfile.open(artifact, "r:") as archive:
                names = {member.name for member in archive.getmembers()}

            for path in secret_files:
                relative = path.relative_to(self.root).as_posix()
                self.assertFalse((snapshot / relative).exists(), relative)
                self.assertNotIn(relative, names)

            self.assertIn("tests/test_sample.py", names)
            for current, _dirs, _files in __import__("os").walk(snapshot, topdown=False):
                Path(current).chmod(0o700)

    def test_artifact_is_digest_addressed_and_contains_regular_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            artifact, digest = self.runner._create_content_addressed_artifact(Path(tmp) / "workspace.tar")
            self.assertEqual(digest, hashlib.sha256(artifact.read_bytes()).hexdigest())
            with tarfile.open(artifact, "r:") as archive:
                members = archive.getmembers()
            names = {member.name for member in members}
            self.assertIn("tests/test_sample.py", names)
            self.assertTrue(all(member.isfile() for member in members))

if __name__ == "__main__":
    unittest.main()
