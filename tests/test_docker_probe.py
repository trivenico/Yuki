import os
import tempfile
import unittest
from pathlib import Path

from core.docker_probe import build_docker_info_probe
from core.sandbox_runtime import SandboxRuntimeError


@unittest.skipUnless(os.name == "posix", "POSIX executable semantics required")
class DockerProbePreflightTests(unittest.TestCase):
    def test_builds_fixed_read_only_diagnostics_argv_without_execution(self):
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "docker"
            executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            executable.chmod(0o700)
            plan = build_docker_info_probe(str(executable))
            self.assertEqual(plan.argv, (str(executable), "info", "--format", "{{json .}}"))
            self.assertFalse(plan.executed)
            self.assertFalse(plan.attested)

    def test_rejects_relative_path(self):
        with self.assertRaises(SandboxRuntimeError):
            build_docker_info_probe("docker")

    def test_rejects_missing_file(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(SandboxRuntimeError):
                build_docker_info_probe(str(Path(directory) / "missing"))

    def test_rejects_symlink(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "docker-real"
            target.write_text("binary placeholder", encoding="utf-8")
            target.chmod(0o700)
            link = Path(directory) / "docker"
            link.symlink_to(target)
            with self.assertRaises(SandboxRuntimeError):
                build_docker_info_probe(str(link))

    def test_rejects_non_executable_file(self):
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "docker"
            executable.write_text("placeholder", encoding="utf-8")
            executable.chmod(0o600)
            with self.assertRaises(SandboxRuntimeError):
                build_docker_info_probe(str(executable))


if __name__ == "__main__":
    unittest.main()
