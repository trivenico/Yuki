"""Regression tests for Docker sandbox security controls."""
import io
import json
import tempfile
import unittest
from unittest import mock
from pathlib import Path

from hackback.sandbox import DockerSandbox, SandboxError


IMAGE = "python@sha256:" + ("a" * 64)


class FakeProcess:
    def __init__(self, command, **kwargs):
        self.command = command
        self.kwargs = kwargs
        self.stdout = io.BytesIO(b'{"ok": true}')
        self.stderr = io.BytesIO(b"")
        self.returncode = 0
        self.pid = 99999999
        self.terminated = False
        self.killed = False

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.returncode = -15

    def kill(self):
        self.killed = True
        self.returncode = -9


class DockerSandboxTests(unittest.TestCase):
    def sandbox(self, workspace, **kwargs):
        return DockerSandbox(
            workspace,
            allowed_images=[IMAGE],
            enabled=True,
            approver=lambda _reason: True,
            **kwargs,
        )

    def test_workspace_is_read_only_by_default_and_container_is_restricted(self):
        with tempfile.TemporaryDirectory() as directory:
            sandbox = self.sandbox(directory)
            fake = FakeProcess
            with mock.patch("hackback.sandbox.subprocess.Popen", side_effect=fake) as popen:
                result = sandbox.run(IMAGE, ["python", "-c", "print('ok')"])
            self.assertEqual(result["returncode"], 0)
            command = popen.call_args.args[0]
            self.assertIn("--network=none", command)
            self.assertIn("--read-only", command)
            self.assertIn("--cap-drop=ALL", command)
            self.assertIn("--memory-swap=512m", command)
            self.assertIn("--pids-limit=64", command)
            mount = command[command.index("--mount") + 1]
            self.assertTrue(mount.endswith(",ro"))
            self.assertFalse(popen.call_args.kwargs["shell"])

    def test_writable_workspace_requires_explicit_opt_in(self):
        with tempfile.TemporaryDirectory() as directory:
            sandbox = self.sandbox(directory, writable_workspace=True)
            with mock.patch("hackback.sandbox.subprocess.Popen", side_effect=FakeProcess) as popen:
                sandbox.run(IMAGE, ["worker"])
            mount = popen.call_args.args[0][popen.call_args.args[0].index("--mount") + 1]
            self.assertTrue(mount.endswith(",rw"))

    def test_unapproved_image_is_rejected_before_process_start(self):
        with tempfile.TemporaryDirectory() as directory:
            sandbox = self.sandbox(directory)
            with mock.patch("hackback.sandbox.subprocess.Popen") as popen:
                with self.assertRaises(SandboxError):
                    sandbox.run("python:latest", ["worker"])
            popen.assert_not_called()

    def test_root_workspace_is_rejected(self):
        with self.assertRaises(ValueError):
            DockerSandbox("/")

    def test_output_overflow_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            sandbox = self.sandbox(directory)

            class OverflowProcess(FakeProcess):
                def __init__(self, command, **kwargs):
                    super().__init__(command, **kwargs)
                    self.stdout = io.BytesIO(b"x" * (sandbox.OUTPUT_LIMIT_BYTES + 1))

            with mock.patch("hackback.sandbox.subprocess.Popen", side_effect=OverflowProcess):
                with self.assertRaisesRegex(SandboxError, "output exceeded"):
                    sandbox.run(IMAGE, ["worker"])

    def test_configured_seccomp_and_apparmor_profiles_are_passed_to_docker(self):
        with tempfile.TemporaryDirectory() as directory:
            profile_path = Path(directory) / "seccomp.json"
            profile_path.write_text(json.dumps({"defaultAction": "SCMP_ACT_ERRNO", "syscalls": []}))
            sandbox = self.sandbox(
                directory,
                seccomp_profile=profile_path,
                apparmor_profile="docker-default",
            )
            with mock.patch("hackback.sandbox.subprocess.Popen", side_effect=FakeProcess) as popen:
                sandbox.run(IMAGE, ["worker"])
            command = popen.call_args.args[0]
            self.assertIn(f"--security-opt=seccomp={profile_path.resolve()}", command)
            self.assertIn("--security-opt=apparmor=docker-default", command)

    def test_seccomp_default_allow_profile_fails_closed_before_start(self):
        with tempfile.TemporaryDirectory() as directory:
            profile_path = Path(directory) / "seccomp.json"
            profile_path.write_text(json.dumps({"defaultAction": "SCMP_ACT_ALLOW", "syscalls": []}))
            sandbox = self.sandbox(directory, seccomp_profile=profile_path)
            with mock.patch("hackback.sandbox.subprocess.Popen") as popen:
                with self.assertRaisesRegex(SandboxError, "fail-closed"):
                    sandbox.run(IMAGE, ["worker"])
            popen.assert_not_called()

    def test_seccomp_malformed_syscall_rules_fail_closed_before_start(self):
        invalid_profiles = [
            {"defaultAction": "SCMP_ACT_ERRNO", "syscalls": ["read"]},
            {"defaultAction": "SCMP_ACT_ERRNO", "syscalls": [{"action": "SCMP_ACT_ALLOW", "names": []}]},
            {"defaultAction": "SCMP_ACT_ERRNO", "syscalls": [{"action": "SCMP_ACT_ALLOW", "names": ["read"], "args": "bad"}]},
        ]
        for profile in invalid_profiles:
            with self.subTest(profile=profile), tempfile.TemporaryDirectory() as directory:
                profile_path = Path(directory) / "seccomp.json"
                profile_path.write_text(json.dumps(profile))
                sandbox = self.sandbox(directory, seccomp_profile=profile_path)
                with mock.patch("hackback.sandbox.subprocess.Popen") as popen:
                    with self.assertRaises(SandboxError):
                        sandbox.run(IMAGE, ["worker"])
                popen.assert_not_called()
    def test_seccomp_invalid_top_level_schema_fails_before_start(self):
        invalid_profiles = [
            {"defaultAction": "SCMP_ACT_ERRNO", "syscalls": [], "unexpected": True},
            {"defaultAction": "SCMP_ACT_ERRNO", "syscalls": [], "defaultErrnoRet": 0},
            {"defaultAction": "SCMP_ACT_ERRNO", "syscalls": [], "defaultErrnoRet": True},
            {"defaultAction": "SCMP_ACT_ERRNO", "syscalls": [], "architectures": "SCMP_ARCH_X86_64"},
            {"defaultAction": "SCMP_ACT_ERRNO", "syscalls": [], "flags": ["UNKNOWN_FLAG"]},
            {"defaultAction": "SCMP_ACT_ERRNO", "syscalls": [], "architectures": ["SCMP_ARCH_X86_64", "SCMP_ARCH_X86_64"]},
        ]
        for profile in invalid_profiles:
            with self.subTest(profile=profile), tempfile.TemporaryDirectory() as directory:
                profile_path = Path(directory) / "seccomp.json"
                profile_path.write_text(json.dumps(profile))
                sandbox = self.sandbox(directory, seccomp_profile=profile_path)
                with mock.patch("hackback.sandbox.subprocess.Popen") as popen:
                    with self.assertRaises(SandboxError):
                        sandbox.run(IMAGE, ["worker"])
                popen.assert_not_called()
    def test_missing_seccomp_profile_fails_closed_before_start(self):
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / "missing.json"
            sandbox = self.sandbox(directory, seccomp_profile=missing)
            with mock.patch("hackback.sandbox.subprocess.Popen") as popen:
                with self.assertRaisesRegex(SandboxError, "does not exist"):
                    sandbox.run(IMAGE, ["worker"])
            popen.assert_not_called()

    def test_invalid_apparmor_profile_name_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "unsupported characters"):
                self.sandbox(directory, apparmor_profile="profile; --privileged")

    def test_explicit_approval_is_required(self):
        with tempfile.TemporaryDirectory() as directory:
            sandbox = DockerSandbox(
                directory, allowed_images=[IMAGE], enabled=True, approver=lambda _reason: False
            )
            with mock.patch("hackback.sandbox.subprocess.Popen") as popen:
                with self.assertRaisesRegex(SandboxError, "approval"):
                    sandbox.run(IMAGE, ["worker"])
            popen.assert_not_called()


if __name__ == "__main__":
    unittest.main()
