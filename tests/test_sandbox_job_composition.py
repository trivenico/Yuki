from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from automation.sandbox_job_composition import (
    SandboxJobCompositionError,
    build_sandbox_job_recovery,
)
from api.production_runtime import build_production_conversation_runtime
from core.config import load_config


class SandboxJobCompositionTests(unittest.TestCase):
    def test_disabled_mode_does_not_initialize_docker_or_storage(self):
        with patch("automation.sandbox_job_composition.shutil.which") as which:
            self.assertIsNone(build_sandbox_job_recovery({"sandbox_jobs_enabled": False}))
        which.assert_not_called()

    def test_enabled_mode_requires_all_fixed_runtime_configuration(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "jobs"
            root.mkdir(mode=0o700)
            os.chmod(root, 0o700)
            config = {
                "sandbox_jobs_enabled": True,
                "sandbox_jobs_root": str(root),
                "workspace": temp,
                "sandbox_jobs_image": "registry.example/yuki@sha256:" + "a" * 64,
                "sandbox_jobs_command": [],
            }
            with self.assertRaises(SandboxJobCompositionError):
                build_sandbox_job_recovery(config)

    def test_enabled_mode_builds_recovery_and_persists_private_databases(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "jobs"
            root.mkdir(mode=0o700)
            os.chmod(root, 0o700)
            config = {
                "sandbox_jobs_enabled": True,
                "sandbox_jobs_root": str(root),
                "workspace": temp,
                "sandbox_jobs_image": "registry.example/yuki@sha256:" + "a" * 64,
                "sandbox_jobs_command": ["/usr/local/bin/yuki-job"],
                "sandbox_jobs_docker_binary": "docker",
            }
            runtime = Mock()
            runtime.handle_for_job = Mock()
            runtime.reconcile = Mock()
            with patch("automation.sandbox_job_composition.shutil.which", return_value="/usr/bin/docker"), patch(
                "automation.sandbox_job_composition.DockerJobRuntime", return_value=runtime
            ), patch("automation.sandbox_job_composition.SandboxJobRecovery") as recovery_type:
                recovery_type.return_value = object()
                result = build_sandbox_job_recovery(config)

            self.assertIs(result, recovery_type.return_value)
            recovery_type.assert_called_once()
            self.assertEqual((root / "sandbox-jobs.sqlite3").stat().st_mode & 0o777, 0o600)
            self.assertEqual((root / "sandbox-recovery-audit.sqlite3").stat().st_mode & 0o777, 0o600)
            self.assertEqual(recovery_type.call_args.kwargs["runtime"], runtime)
            self.assertTrue(callable(recovery_type.call_args.kwargs["audit"]))

    def test_production_factory_always_injects_recovery_dependency(self):
        runtime = object()
        with patch("api.production_runtime.build_sandbox_job_recovery", return_value=None), patch(
            "api.production_runtime.build_conversation_runtime", return_value=runtime
        ) as builder:
            result = build_production_conversation_runtime(
                {"sandbox_jobs_enabled": False}, auth_service=object(), agent_loop=object()
            )
        self.assertIs(result, runtime)
        self.assertIsNone(builder.call_args.kwargs["sandbox_job_recovery"])

    def test_config_parses_separate_disabled_by_default_setting(self):
        with tempfile.TemporaryDirectory() as temp:
            config_path = Path(temp) / "config.yaml"
            config_path.write_text("workspace: " + temp + "\\nsandbox_jobs_enabled: false\\n", encoding="utf-8")
            config = load_config(config_path)
        self.assertFalse(config["sandbox_jobs_enabled"])
        self.assertEqual(config["sandbox_jobs_command"], [])

    def test_config_rejects_enabled_jobs_without_digest_pinned_image(self):
        with tempfile.TemporaryDirectory() as temp:
            config_path = Path(temp) / "config.yaml"
            config_path.write_text(
                "workspace: " + temp + "\\n"
                "sandbox_jobs_enabled: true\\n"
                "sandbox_jobs_root: jobs\\n"
                "sandbox_jobs_image: latest\\n"
                "sandbox_jobs_command: [/bin/true]\\n",
                encoding="utf-8",
            )
            with self.assertRaises(RuntimeError):
                load_config(config_path)

    def test_production_factory_injects_recovery_when_enabled(self):
        runtime = object()
        recovery = object()
        with patch("api.production_runtime.build_sandbox_job_recovery", return_value=recovery), patch(
            "api.production_runtime.build_conversation_runtime", return_value=runtime
        ) as builder:
            result = build_production_conversation_runtime(
                {"sandbox_jobs_enabled": True}, auth_service=object(), agent_loop=object()
            )
        self.assertIs(result, runtime)
        self.assertIs(builder.call_args.kwargs["sandbox_job_recovery"], recovery)

    def test_production_factory_rejects_caller_supplied_recovery(self):
        with self.assertRaises(ValueError):
            build_production_conversation_runtime(
                {"sandbox_jobs_enabled": False}, sandbox_job_recovery=object()
            )


if __name__ == "__main__":
    unittest.main()
