import hashlib
import os
import tempfile
import unittest
from pathlib import Path

from automation.approved_workspace_changes import (
    ApprovedTestRunner,
    ApprovedWorkspaceChanges,
    FileChange,
    WorkspaceChangeError,
)
from automation.authorized_read_tools import ToolAuthorizationError, ToolInputError, ToolPrincipal


class ApprovedWorkspaceChangesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.file = self.root / "sample.py"
        self.file.write_text("VALUE = 1\n", encoding="utf-8")
        (self.root / ".env").write_text("TOKEN=secret\n", encoding="utf-8")
        self.principal = ToolPrincipal("alice", frozenset({"workspace:write", "tests:execute"}))
        self.audit_events = []
        self.approved = True
        self.approval_calls = []

    def trusted(self, principal):
        return principal.actor_id == "alice"

    def approve(self, actor, change_id, digest):
        self.approval_calls.append((actor, change_id, digest))
        return self.approved

    def service(self, allowed=("sample.py",)):
        return ApprovedWorkspaceChanges(
            self.root, trusted_principal=self.trusted, approve_change=self.approve,
            audit=self.audit_events.append, allowed_paths=allowed,
        )

    def change(self, path="sample.py", content="VALUE = 2\n"):
        old = (self.root / path).read_bytes()
        return FileChange(path, hashlib.sha256(old).hexdigest(), content)

    def test_edit_requires_permission_and_digest_bound_approval(self):
        result = self.service().apply(principal=self.principal, changes=[self.change()])
        self.assertEqual(self.file.read_text(encoding="utf-8"), "VALUE = 2\n")
        self.assertEqual(result.changed_paths, ("sample.py",))
        self.assertEqual(self.approval_calls[0][2], result.content_digest)
        self.assertEqual(self.audit_events[-1]["outcome"], "succeeded")

    def test_denied_approval_does_not_change_file(self):
        self.approved = False
        with self.assertRaises(ToolAuthorizationError):
            self.service().apply(principal=self.principal, changes=[self.change()])
        self.assertEqual(self.file.read_text(encoding="utf-8"), "VALUE = 1\n")

    def test_stale_content_digest_rejected(self):
        change = self.change()
        self.file.write_text("VALUE = 99\n", encoding="utf-8")
        with self.assertRaises(WorkspaceChangeError):
            self.service().apply(principal=self.principal, changes=[change])
        self.assertEqual(self.approval_calls, [])

    def test_unlisted_secret_and_parent_paths_rejected(self):
        with self.assertRaises(ToolAuthorizationError):
            self.service().apply(principal=self.principal, changes=[self.change(path=".env", content="x")])
        with self.assertRaises(ToolAuthorizationError):
            self.service(allowed=("../outside.py",)).apply(
                principal=self.principal,
                changes=[FileChange("../outside.py", "0" * 64, "x")],
            )

    def test_symlink_paths_rejected(self):
        target = self.root / "target.py"
        target.write_text("x=1\n", encoding="utf-8")
        link = self.root / "link.py"
        try:
            link.symlink_to(target)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks unavailable")
        with self.assertRaises(ToolInputError):
            self.service(allowed=("link.py",)).apply(
                principal=self.principal, changes=[self.change(path="link.py", content="x=2\n")]
            )

    def test_missing_permission_and_audit_failure_fail_closed(self):
        read_only = ToolPrincipal("alice", frozenset({"workspace:read"}))
        with self.assertRaises(ToolAuthorizationError):
            self.service().apply(principal=read_only, changes=[self.change()])
        broken = ApprovedWorkspaceChanges(
            self.root, trusted_principal=self.trusted, approve_change=self.approve,
            audit=lambda _event: (_ for _ in ()).throw(OSError("audit down")),
            allowed_paths=("sample.py",),
        )
        with self.assertRaises(RuntimeError):
            broken.apply(principal=self.principal, changes=[self.change()])
        self.assertEqual(self.file.read_text(encoding="utf-8"), "VALUE = 1\n")

    def test_duplicate_paths_and_excessive_changes_rejected(self):
        change = self.change()
        with self.assertRaises(ToolInputError):
            self.service().apply(principal=self.principal, changes=[change, change])
        with self.assertRaises(ToolInputError):
            self.service().apply(principal=self.principal, changes=[change] * 9)


class ApprovedTestRunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "test_sample.py").write_text(
            "import unittest\nclass T(unittest.TestCase):\n"
            " def test_ok(self): self.assertEqual(2 + 2, 4)\n"
            "if __name__ == '__main__': unittest.main()\n",
            encoding="utf-8",
        )
        self.audit_events = []
        self.approved = True
        self.calls = []
        self.principal = ToolPrincipal("alice", frozenset({"tests:execute"}))

    def runner(self, modules=("test_sample",), timeout=10):
        return ApprovedTestRunner(
            self.root, trusted_principal=lambda p: p.actor_id == "alice",
            approve_run=lambda actor, run_id, digest: (
                self.calls.append((actor, run_id, digest)) or self.approved
            ),
            audit=self.audit_events.append, allowed_test_modules=modules,
            timeout_seconds=timeout,
        )

    def test_allowlisted_test_runs_without_shell_and_records_result(self):
        result = self.runner().run(principal=self.principal, test_module="test_sample")
        self.assertEqual(result.exit_code, 0)
        self.assertFalse(result.timed_out)
        self.assertIn("OK", result.stderr + result.stdout)
        self.assertEqual(self.audit_events[0]["outcome"], "started")
        self.assertEqual(self.audit_events[-1]["outcome"], "succeeded")

    def test_unknown_target_or_denied_approval_does_not_execute(self):
        with self.assertRaises(ToolAuthorizationError):
            self.runner().run(principal=self.principal, test_module="other_module")
        self.approved = False
        with self.assertRaises(ToolAuthorizationError):
            self.runner().run(principal=self.principal, test_module="test_sample")
        self.assertFalse(any(event["outcome"] == "started" for event in self.audit_events))

    def test_test_source_digest_is_part_of_approval(self):
        self.runner().run(principal=self.principal, test_module="test_sample")
        self.assertEqual(len(self.calls[0][2]), 64)
        (self.root / "test_sample.py").write_text("import unittest\n", encoding="utf-8")
        self.runner().run(principal=self.principal, test_module="test_sample")
        self.assertNotEqual(self.calls[0][2], self.calls[1][2])

    def test_timeout_is_bounded(self):
        (self.root / "test_sample.py").write_text(
            "import time, unittest\nclass T(unittest.TestCase):\n"
            " def test_slow(self): time.sleep(2)\n",
            encoding="utf-8",
        )
        result = self.runner(timeout=1).run(principal=self.principal, test_module="test_sample")
        self.assertTrue(result.timed_out)
        self.assertEqual(result.exit_code, 124)

    def test_invalid_allowlist_and_timeout_rejected(self):
        with self.assertRaises(ValueError):
            self.runner(modules=("test_sample; rm -rf /",))
        with self.assertRaises(ValueError):
            self.runner(timeout=121)


if __name__ == "__main__":
    unittest.main()
