import json
import os
from pathlib import Path
import tempfile
import unittest

from providers.kaggle_compute import (
    KaggleIntegrationError,
    KaggleKernelSpec,
    build_kernel_push_argv,
    discover_kaggle_auth,
)


class KaggleComputeTests(unittest.TestCase):
    def test_detects_environment_token_without_exposing_value(self):
        status = discover_kaggle_auth(environ={"KAGGLE_API_TOKEN": "secret-token"}, home="/tmp")
        self.assertTrue(status.configured)
        self.assertEqual(status.source, "environment_token")
        self.assertNotIn("secret-token", repr(status))

    def test_detects_private_access_token_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".kaggle"
            path.mkdir(mode=0o700)
            token = path / "access_token"
            token.write_text("KGAT-example")
            token.chmod(0o600)
            status = discover_kaggle_auth(environ={}, home=directory)
            self.assertTrue(status.configured)
            self.assertEqual(status.source, "access_token_file")

    def test_rejects_world_readable_credentials(self):
        if os.name == "nt":
            self.skipTest("POSIX mode bits are not enforced on Windows")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".kaggle"
            path.mkdir(mode=0o700)
            token = path / "access_token"
            token.write_text("KGAT-example")
            token.chmod(0o644)
            status = discover_kaggle_auth(environ={}, home=directory)
            self.assertFalse(status.configured)
            self.assertEqual(status.warning, "credential_file_permissions_too_open")

    def test_detects_valid_legacy_credentials_without_returning_secret(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".kaggle"
            path.mkdir(mode=0o700)
            creds = path / "kaggle.json"
            creds.write_text(json.dumps({"username": "user", "key": "secret"}))
            creds.chmod(0o600)
            status = discover_kaggle_auth(environ={}, home=directory)
            self.assertTrue(status.configured)
            self.assertEqual(status.source, "legacy_credentials_file")
            self.assertNotIn("secret", repr(status))

    def test_missing_auth_returns_setup_guidance(self):
        with tempfile.TemporaryDirectory() as directory:
            status = discover_kaggle_auth(environ={}, home=directory)
            self.assertFalse(status.configured)
            self.assertEqual(status.warning, "run_kaggle_auth_login_or_configure_api_token")

    def test_requires_explicit_approval_and_valid_spec(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            (folder / "kernel-metadata.json").write_text("{}")
            with self.assertRaises(KaggleIntegrationError):
                build_kernel_push_argv(KaggleKernelSpec("owner/yuki", str(folder)))
            with self.assertRaises(KaggleIntegrationError):
                build_kernel_push_argv(KaggleKernelSpec("owner;bad/yuki", str(folder), approved=True))

    def test_builds_accelerator_command_only_after_approval(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            (folder / "kernel-metadata.json").write_text("{}")
            argv = build_kernel_push_argv(
                KaggleKernelSpec("owner/yuki", str(folder), "NvidiaTeslaT4",
                                 timeout_seconds=1800, approved=True),
                executable="/usr/bin/kaggle",
            )
            self.assertEqual(argv[:4], ["/usr/bin/kaggle", "kernels", "push", "-p"])
            self.assertIn("--accelerator", argv)
            self.assertIn("NvidiaTeslaT4", argv)

    def test_rejects_missing_folder_and_invalid_accelerator(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(KaggleIntegrationError):
                build_kernel_push_argv(
                    KaggleKernelSpec("owner/yuki", str(Path(directory) / "missing"),
                                     approved=True),
                    executable="/usr/bin/kaggle",
                )
            folder = Path(directory)
            (folder / "kernel-metadata.json").write_text("{}")
            with self.assertRaises(KaggleIntegrationError):
                build_kernel_push_argv(
                    KaggleKernelSpec("owner/yuki", str(folder), "UntrustedGPU",
                                     approved=True),
                    executable="/usr/bin/kaggle",
                )


if __name__ == "__main__":
    unittest.main()
