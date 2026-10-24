import hashlib
from pathlib import Path
import tempfile
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


if __name__ == "__main__":
    unittest.main()
