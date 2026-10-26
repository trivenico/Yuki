import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from automation.approved_workspace_changes import FileChange, WorkspaceChangeError
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
            allow_host_test_execution=True,
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

    def test_staging_copy_excludes_sensitive_files_and_symlinks(self):
        (self.root / ".env").write_text("API_KEY=not-a-real-key", encoding="utf-8")
        (self.root / "api_token.txt").write_text("not-a-real-token", encoding="utf-8")
        (self.root / "private_key.pem").write_text("not-a-real-key", encoding="utf-8")
        (self.root / "ordinary.txt").write_text("safe", encoding="utf-8")
        link = self.root / "linked.txt"
        try:
            link.symlink_to(self.root / "ordinary.txt")
        except (OSError, NotImplementedError):
            self.skipTest("symlinks are unavailable on this platform")
        with tempfile.TemporaryDirectory() as destination:
            staged = Path(destination) / "workspace"
            staged.mkdir()
            self.coordinator._copy_workspace(staged)
            self.assertFalse((staged / ".env").exists())
            self.assertFalse((staged / "api_token.txt").exists())
            self.assertFalse((staged / "private_key.pem").exists())
            self.assertFalse((staged / "linked.txt").exists())
            self.assertEqual((staged / "ordinary.txt").read_text(encoding="utf-8"), "safe")

    def test_staging_rejects_file_swapped_for_external_symlink_at_open_boundary(self):
        with tempfile.TemporaryDirectory() as external:
            outside = Path(external) / "outside.txt"
            outside.write_text("must never be copied", encoding="utf-8")
            target = self.root / "ordinary-race.txt"
            target.write_text("initial safe content", encoding="utf-8")
            original_open_component = IsolatedUpgradeCoordinator._open_component

            def swap_before_leaf_open(name, flags, *, dir_fd):
                if name == target.name:
                    target.unlink()
                    target.symlink_to(outside)
                return original_open_component(name, flags, dir_fd=dir_fd)

            with tempfile.TemporaryDirectory() as destination:
                staged = Path(destination) / "workspace"
                staged.mkdir()
                with patch.object(IsolatedUpgradeCoordinator, "_open_component", side_effect=swap_before_leaf_open):
                    self.coordinator._copy_workspace(staged)
                self.assertFalse((staged / target.name).exists())
                self.assertNotEqual((staged / target.name).read_text(encoding="utf-8") if (staged / target.name).exists() else "", outside.read_text(encoding="utf-8"))

    def test_staging_fails_closed_when_file_cannot_be_inspected(self):
        blocked = self.root / "unreadable.txt"
        blocked.write_text("content", encoding="utf-8")
        original_open_component = IsolatedUpgradeCoordinator._open_component

        def fail_open(path_name, flags, *, dir_fd):
            if path_name == blocked.name:
                raise OSError("simulated descriptor open failure")
            return original_open_component(path_name, flags, dir_fd=dir_fd)

        with tempfile.TemporaryDirectory() as destination:
            staged = Path(destination) / "workspace"
            staged.mkdir()
            with patch.object(IsolatedUpgradeCoordinator, "_open_component", side_effect=fail_open):
                with self.assertRaisesRegex(WorkspaceChangeError, "unable to safely open"):
                    self.coordinator._copy_workspace(staged)

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

    def test_failed_test_stops_remaining_targets_and_never_promotes(self):
        old = self.file.read_text(encoding="utf-8")
        change = FileChange("src/example.py", hashlib.sha256(old.encode()).hexdigest(), "VALUE = 2\n")
        coordinator = IsolatedUpgradeCoordinator(
            self.root,
            trusted_principal=lambda p: p.actor_id == "test-operator",
            approve_change=lambda actor, change_id, digest: True,
            approve_test=lambda actor, run_id, digest: True,
            audit=lambda event: self.audit_events.append(event),
            allowed_paths=["src/example.py"],
            allowed_test_modules=["tests.test_example", "tests.test_second"],
            allow_host_test_execution=True,
        )
        failed = SimpleNamespace(
            target="tests.test_example", exit_code=1, timed_out=False,
            duration_seconds=0.01, stdout="", stderr="failure",
        )
        with patch(
            "automation.self_upgrade_coordinator.ApprovedWorkspaceChanges.apply",
            return_value=SimpleNamespace(content_digest="change-digest"),
        ), patch(
            "automation.self_upgrade_coordinator.ApprovedTestRunner.run",
            return_value=failed,
        ) as run:
            result = coordinator.validate(principal=self.principal, changes=[change])
        self.assertEqual(result["outcome"], "failed")
        self.assertFalse(result["promotion_allowed"])
        self.assertEqual(len(result["test_targets"]), 1)
        run.assert_called_once()
        self.assertEqual(self.file.read_text(encoding="utf-8"), old)

    def test_workspace_change_failure_prevents_test_execution(self):
        old = self.file.read_text(encoding="utf-8")
        change = FileChange("src/example.py", hashlib.sha256(old.encode()).hexdigest(), "VALUE = 2\\n")
        with patch(
            "automation.self_upgrade_coordinator.ApprovedWorkspaceChanges.apply",
            side_effect=WorkspaceChangeError("simulated change failure"),
        ), patch(
            "automation.self_upgrade_coordinator.ApprovedTestRunner.run",
        ) as run:
            with self.assertRaisesRegex(WorkspaceChangeError, "simulated change failure"):
                self.coordinator.validate(principal=self.principal, changes=[change])
        run.assert_not_called()
        self.assertEqual(self.file.read_text(encoding="utf-8"), old)

    def test_host_test_execution_is_disabled_by_default(self):
        old = self.file.read_text(encoding="utf-8")
        change = FileChange("src/example.py", hashlib.sha256(old.encode()).hexdigest(), "VALUE = 2\\n")
        coordinator = IsolatedUpgradeCoordinator(
            self.root,
            trusted_principal=lambda p: p.actor_id == "test-operator",
            approve_change=lambda actor, change_id, digest: True,
            approve_test=lambda actor, run_id, digest: True,
            audit=lambda event: self.audit_events.append(event),
            allowed_paths=["src/example.py"],
            allowed_test_modules=["tests.test_example"],
        )
        with patch("automation.self_upgrade_coordinator.ApprovedTestRunner.run") as run:
            with self.assertRaisesRegex(ToolAuthorizationError, "host test execution is disabled"):
                coordinator.validate(principal=self.principal, changes=[change])
        run.assert_not_called()
        self.assertEqual(self.file.read_text(encoding="utf-8"), old)

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


    def test_isolated_runner_factory_is_used_when_host_execution_disabled(self):
        old = self.file.read_text(encoding="utf-8")
        change = FileChange("src/example.py", hashlib.sha256(old.encode()).hexdigest(), "VALUE = 2\\n")
        calls = []
        fake_runner = type("FakeIsolatedRunner", (), {
            "run": lambda _self, *, principal, test_module: (
                calls.append((principal.actor_id, test_module)) or
                SimpleNamespace(target=test_module, exit_code=0, timed_out=False,
                                duration_seconds=0.01, stdout="isolated", stderr="")
            )
        })()
        coordinator = IsolatedUpgradeCoordinator(
            self.root,
            trusted_principal=lambda p: p.actor_id == "test-operator",
            approve_change=lambda actor, change_id, digest: True,
            approve_test=lambda actor, run_id, digest: True,
            audit=lambda event: self.audit_events.append(event),
            allowed_paths=["src/example.py"],
            allowed_test_modules=["tests.test_example"],
            isolated_runner_factory=lambda staged_root: (
                self.assertTrue(staged_root.is_dir()) or fake_runner
            ),
        )
        with patch(
            "automation.self_upgrade_coordinator.ApprovedWorkspaceChanges.apply",
            return_value=SimpleNamespace(content_digest="change-digest"),
        ):
            result = coordinator.validate(principal=self.principal, changes=[change])
        self.assertEqual(result["outcome"], "passed")
        self.assertFalse(result["promotion_allowed"])
        self.assertEqual(calls, [("test-operator", "tests.test_example")])
        self.assertEqual(self.file.read_text(encoding="utf-8"), old)


if __name__ == "__main__":
    unittest.main()
