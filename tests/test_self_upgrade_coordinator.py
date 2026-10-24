import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from automation.approved_workspace_changes import FileChange
from automation.authorized_read_tools import ToolPrincipal, ToolAuthorizationError
from automation.self_upgrade_coordinator import IsolatedUpgradeCoordinator


class IsolatedUpgradeCoordinatorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "src"
        self.source.mkdir()
        self.file = self.source / "example.py"
        self.file.write_text("VALUE = 1\n", encoding="utf-8")
        tests = self.root / "tests"
        tests.mkdir()
        (tests / "__init__.py").write_text("", encoding="utf-8")
        (tests / "test_example.py").write_text("import unittest\nclass T(unittest.TestCase):\n def test_ok(self): self.assertTrue(True)\n", encoding="utf-8")
        self.principal = ToolPrincipal("test-operator", frozenset({"workspace:write", "tests:execute"}))
        self.audit_events = []
        self.coordinator = IsolatedUpgradeCoordinator(
            self.root,
            trusted_principal=lambda p: p.actor_id == "test-operator",
            approve_change=lambda actor, change_id, digest: True,
            approve_test=lambda actor, run_id, digest: True,
            audit=lambda event: self.audit_events.append(event),
            allowed_paths=["src/example.py"],
            allowed_test_modules=["tests.test_example"],
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_test_timeout_is_bounded(self):
        for timeout in (0, 301, True, 1.5):
            with self.subTest(timeout=timeout):
                with self.assertRaises(ValueError):
                    IsolatedUpgradeCoordinator(
                        self.root,
                        trusted_principal=lambda p: True,
                        approve_change=lambda actor, change_id, digest: True,
                        approve_test=lambda actor, run_id, digest: True,
                        audit=lambda event: None,
                        allowed_paths=["src/example.py"],
                        allowed_test_modules=["tests.test_example"],
                        test_timeout_seconds=timeout,
                    )

    def test_validation_uses_approved_services_and_never_promotes(self):
        old = self.file.read_text(encoding="utf-8")
        change = FileChange("src/example.py", hashlib.sha256(old.encode()).hexdigest(), "VALUE = 2\n")
        with patch("automation.self_upgrade_coordinator.ApprovedWorkspaceChanges.apply",
                   return_value=SimpleNamespace(content_digest="change-digest")) as apply, \
             patch("automation.self_upgrade_coordinator.ApprovedTestRunner.run",
                   return_value=SimpleNamespace(target="tests.test_example", exit_code=0,
                       timed_out=False, duration_seconds=0.01, stdout="ok", stderr="")) as run:
            result = self.coordinator.validate(principal=self.principal, changes=[change])
        self.assertEqual(result["outcome"], "passed")
        self.assertFalse(result["promotion_allowed"])
        apply.assert_called_once()
        run.assert_called_once()
        self.assertEqual(self.file.read_text(encoding="utf-8"), old)
        self.assertEqual(self.audit_events[-1]["action"], "self_upgrade.validation")

    def test_missing_permissions_fail_closed(self):
        principal = ToolPrincipal("test-operator", frozenset({"workspace:write"}))
        old = self.file.read_text(encoding="utf-8")
        change = FileChange("src/example.py", hashlib.sha256(old.encode()).hexdigest(), "VALUE = 2\n")
        with self.assertRaises(ToolAuthorizationError):
            self.coordinator.validate(principal=principal, changes=[change])


    def test_audit_failure_denies_validation_result(self):
        old = self.file.read_text(encoding="utf-8")
        change = FileChange("src/example.py", hashlib.sha256(old.encode()).hexdigest(), "VALUE = 2\n")
        self.coordinator.audit = lambda event: (_ for _ in ()).throw(OSError("audit unavailable"))
        with (
            patch("automation.self_upgrade_coordinator.ApprovedWorkspaceChanges.apply",
                  return_value=SimpleNamespace(content_digest="change-digest")),
            patch("automation.self_upgrade_coordinator.ApprovedTestRunner.run",
                  return_value=SimpleNamespace(target="tests.test_example", exit_code=0,
                      timed_out=False, duration_seconds=0.01, stdout="ok", stderr="")),
        ):
            with self.assertRaisesRegex(RuntimeError, "audit unavailable"):
                self.coordinator.validate(principal=self.principal, changes=[change])
        self.assertEqual(self.file.read_text(encoding="utf-8"), old)



if __name__ == "__main__":
    unittest.main()
