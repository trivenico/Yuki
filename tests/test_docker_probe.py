import os
import socket
import tempfile
import unittest
from pathlib import Path

from core.docker_probe import build_docker_info_probe, run_docker_info_probe
from core.sandbox_runtime import SandboxRuntimeError


VALID_INFO = '{"ServerVersion":"27.1.0","SecurityOptions":["name=rootless","name=seccomp"],"CgroupVersion":"2"}'


@unittest.skipUnless(os.name == "posix", "POSIX process and socket semantics required")
class DockerProbeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.endpoint = self.root / "docker.sock"
        self.server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.server.bind(str(self.endpoint))
        self.addCleanup(self.server.close)

    def executable(self, body):
        path = self.root / "docker"
        path.write_text("#!/bin/sh\n" + body + "\n", encoding="utf-8")
        path.chmod(0o700)
        return str(path)

    def plan(self, body):
        return build_docker_info_probe(self.executable(body), str(self.endpoint))

    def test_builds_fixed_explicit_endpoint_command(self):
        plan = self.plan("exit 0")
        self.assertEqual(plan.argv[1:3], ("--host", "unix://" + str(self.endpoint)))
        self.assertFalse(plan.executed)
        self.assertFalse(plan.attested)

    def test_rejects_relative_executable_and_symlink(self):
        with self.assertRaises(SandboxRuntimeError):
            build_docker_info_probe("docker", str(self.endpoint))
        link = self.root / "docker-link"
        link.symlink_to(self.executable("exit 0"))
        with self.assertRaises(SandboxRuntimeError):
            build_docker_info_probe(str(link), str(self.endpoint))

    def test_rejects_non_socket_endpoint(self):
        ordinary = self.root / "not-a-socket"
        ordinary.write_text("x", encoding="utf-8")
        with self.assertRaises(SandboxRuntimeError):
            build_docker_info_probe(self.executable("exit 0"), str(ordinary))

    def test_reads_diagnostics_but_never_attests(self):
        plan = self.plan("printf '%s' '" + VALID_INFO + "'")
        result = run_docker_info_probe(plan)
        self.assertEqual(result.diagnostics.server_version, "27.1.0")
        self.assertTrue(result.diagnostics.rootless_reported)
        self.assertTrue(result.executed)
        self.assertFalse(result.attested)

    def test_rejects_stdout_overflow(self):
        plan = self.plan("yes x")
        with self.assertRaisesRegex(SandboxRuntimeError, "stdout exceeded"):
            run_docker_info_probe(plan, timeout_seconds=2)

    def test_rejects_stderr_overflow(self):
        plan = self.plan("yes x >&2")
        with self.assertRaisesRegex(SandboxRuntimeError, "stderr exceeded"):
            run_docker_info_probe(plan, timeout_seconds=2)

    def test_kills_timed_out_process(self):
        plan = self.plan("sleep 5")
        with self.assertRaisesRegex(SandboxRuntimeError, "timed out"):
            run_docker_info_probe(plan, timeout_seconds=0.2)

    def test_rejects_group_or_world_writable_executable(self):
        executable = Path(self.executable("exit 0"))
        executable.chmod(0o722)
        with self.assertRaisesRegex(SandboxRuntimeError, "ownership or write permissions"):
            build_docker_info_probe(str(executable), str(self.endpoint))

    def test_rejects_unsafe_parent_directory(self):
        unsafe = self.root / "unsafe"
        unsafe.mkdir()
        unsafe.chmod(0o777)
        executable = unsafe / "docker"
        executable.write_text("#!/bin/sh\\nexit 0\\n", encoding="utf-8")
        executable.chmod(0o700)
        with self.assertRaisesRegex(SandboxRuntimeError, "parent directory"):
            build_docker_info_probe(str(executable), str(self.endpoint))

    def test_rejects_world_writable_docker_socket(self):
        self.endpoint.chmod(0o777)
        with self.assertRaisesRegex(SandboxRuntimeError, "world-writable"):
            build_docker_info_probe(self.executable("exit 0"), str(self.endpoint))

    def test_nonzero_exit_is_rejected(self):
        plan = self.plan("echo failed >&2; exit 2")
        with self.assertRaisesRegex(SandboxRuntimeError, "command failed"):
            run_docker_info_probe(plan)


if __name__ == "__main__":
    unittest.main()
