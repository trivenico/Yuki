import hashlib
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from automation.approved_workspace_changes import FileChange
from automation.authorized_read_tools import ToolPrincipal, ToolAuthorizationError
from automation.autonomous_upgrade_test_mode import build_autonomous_upgrade_test_coordinator


class AutonomousUpgradeTestModeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "src").mkdir()
        (self.root / "tests").mkdir()
        self.source = self.root / "src" / "sample.py"
        self.source.write_text("VALUE = 1\n", encoding="utf-8")
        (self.root / "tests" / "__init__.py").write_text("", encoding="utf-8")
        self.principal = ToolPrincipal(
            "test-operator", frozenset({"workspace:write", "tests:execute"})
        )
        self.audit_events = []

    def tearDown(self):
        self.temp.cleanup()

    def _kwargs(self, **overrides):
        kwargs = {
            "trusted_principal": lambda principal: principal.actor_id == "test-operator",
            "audit": self.audit_events.append,
            "allowed_paths": ["src/sample.py"],
            "allowed_test_modules": ["tests.test_sample"],
            "isolated_runner_factory": lambda staged_root: _PassingRunner(),
            "environ": {"YUKI_AUTONOMOUS_UPGRADE_TEST_MODE": "1"},
        }
        kwargs.update(overrides)
        return kwargs

    def test_opt_in_is_required(self):
        with self.assertRaisesRegex(ToolAuthorizationError, "mode is disabled"):
            build_autonomous_upgrade_test_coordinator(
                self.root, **self._kwargs(environ={})
            )

    def test_default_podman_runner_requires_pinned_image_digest(self):
        with self.assertRaisesRegex(ToolAuthorizationError, "official Python 3.11 slim image pinned by a lowercase sha256 digest"):
            build_autonomous_upgrade_test_coordinator(
                self.root,
                **self._kwargs(isolated_runner_factory=None, environ={
                    "YUKI_AUTONOMOUS_UPGRADE_TEST_MODE": "1"
                }),
            )

    def test_default_factory_constructs_podman_adapter_with_digest_and_allowlist(self):
        image = "docker.io/library/python:3.11-slim@sha256:" + "a" * 64
        coordinator = build_autonomous_upgrade_test_coordinator(
            self.root,
            **self._kwargs(
                isolated_runner_factory=None,
                environ={
                    "YUKI_AUTONOMOUS_UPGRADE_TEST_MODE": "1",
                    "YUKI_TEST_WORKER_IMAGE": image,
                },
            ),
        )
        staged = self.root / "staged"
        staged.mkdir()
        with patch(
            "automation.autonomous_upgrade_test_mode.PodmanIsolatedTestRunner"
        ) as runner:
            coordinator.isolated_runner_factory(staged)
        runner.assert_called_once()
        self.assertEqual(runner.call_args.args[0], staged)
        self.assertEqual(runner.call_args.kwargs["image"], image)
        self.assertEqual(
            runner.call_args.kwargs["allowed_test_modules"], ["tests.test_sample"]
        )
        self.assertTrue(callable(runner.call_args.kwargs["approve_run"]))

    def test_autonomous_mode_validates_only_a_staged_copy(self):
        original = self.source.read_bytes()
        change = FileChange(
            "src/sample.py",
            hashlib.sha256(original).hexdigest(),
            "VALUE = 2\n",
        )
        coordinator = build_autonomous_upgrade_test_coordinator(
            self.root, **self._kwargs()
        )
        result = coordinator.validate(principal=self.principal, changes=[change])

        self.assertEqual(result["outcome"], "passed")
        self.assertFalse(result["promotion_allowed"])
        self.assertEqual(self.source.read_bytes(), original)
        self.assertEqual(self.audit_events[-1]["execution_mode"], "autonomous_isolated_test")
        self.assertFalse(self.audit_events[-1]["promotion_allowed"])

    def test_failed_isolated_test_is_reported_without_promotion(self):
        original = self.source.read_bytes()
        change = FileChange(
            "src/sample.py",
            hashlib.sha256(original).hexdigest(),
            "VALUE = 2\n",
        )
        coordinator = build_autonomous_upgrade_test_coordinator(
            self.root,
            **self._kwargs(isolated_runner_factory=lambda staged_root: _FailingRunner()),
        )

        result = coordinator.validate(principal=self.principal, changes=[change])

        self.assertEqual(result["outcome"], "failed")
        self.assertFalse(result["tests_passed"])
        self.assertFalse(result["promotion_allowed"])
        self.assertEqual(result["test_targets"], ["tests.test_sample"])
        self.assertEqual(self.source.read_bytes(), original)
        self.assertEqual(self.audit_events[-1]["outcome"], "failed")
        self.assertFalse(self.audit_events[-1]["promotion_allowed"])


class _PassingRunner:
    """Test double for the isolation adapter contract; not an OS sandbox."""

    def run(self, *, principal, test_module):
        return SimpleNamespace(
            target=test_module, exit_code=0, timed_out=False,
            duration_seconds=0.01, stdout="test double passed", stderr="",
        )


class _FailingRunner:
    """Failure-path test double; it does not execute untrusted code."""

    def run(self, *, principal, test_module):
        return SimpleNamespace(
            target=test_module, exit_code=1, timed_out=False,
            duration_seconds=0.01, stdout="test failure", stderr="assertion failed",
        )


if __name__ == "__main__":
    unittest.main()
