"""Opt-in integration tests against a configured rootless Docker daemon.

Requires YUKI_DOCKER_INTEGRATION=1 and YUKI_TEST_WORKER_IMAGE set to an
approved immutable SHA-256 digest. This test never pulls or builds images.
"""
import os
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from automation.authorized_read_tools import ToolPrincipal
from automation.isolated_upgrade_worker_docker import DockerIsolatedTestRunner

ENABLED = os.environ.get("YUKI_DOCKER_INTEGRATION") == "1"
IMAGE = os.environ.get("YUKI_TEST_WORKER_IMAGE", "")


@unittest.skipUnless(ENABLED, "set YUKI_DOCKER_INTEGRATION=1 to enable runtime integration")
class RootlessDockerIntegrationTests(unittest.TestCase):
    def setUp(self):
        if "@sha256:" not in IMAGE:
            self.skipTest("YUKI_TEST_WORKER_IMAGE must be an immutable image digest")
        self.temp = tempfile.TemporaryDirectory(prefix="yuki-docker-it-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "tests").mkdir()
        (self.root / "tests" / "__init__.py").write_text("", encoding="utf-8")
        (self.root / "tests" / "test_isolation_contract.py").write_text(
            "import os, pathlib, socket, tempfile, unittest\n"
            "class IsolationContract(unittest.TestCase):\n"
            " def test_worker_runs_non_root(self): self.assertNotEqual(os.geteuid(), 0)\n"
            " def test_host_secret_not_forwarded(self): self.assertNotIn('YUKI_INTEGRATION_SECRET', os.environ)\n"
            " def test_root_filesystem_mount_is_read_only(self):\n"
            "  mounts = pathlib.Path('/proc/mounts').read_text().splitlines()\n"
            "  root_mounts = [line.split() for line in mounts if len(line.split()) >= 4 and line.split()[1] == '/']\n"
            "  self.assertTrue(root_mounts)\n"
            "  self.assertIn('ro', root_mounts[0][3].split(','))\n"
            " def test_artifact_mount_is_read_only(self):\n"
            "  with self.assertRaises(OSError): pathlib.Path('/tmp/worker-artifact-escape').write_text('no')\n"
            " def test_bounded_tmpfs_is_writable(self):\n"
            "  with tempfile.NamedTemporaryFile(dir='/work') as scratch: self.assertTrue(pathlib.Path(scratch.name).exists())\n"
            " def test_network_is_unavailable(self):\n"
            "  s=socket.socket(); s.settimeout(1)\n"
            "  try:\n"
            "   with self.assertRaises(OSError): s.connect(('1.1.1.1', 443))\n"
            "  finally: s.close()\n",
            encoding="utf-8",
        )
        self.principal = ToolPrincipal("docker-integration", frozenset({"tests:execute"}))
        self.events = []

    def test_runtime_enforces_basic_isolation_contract(self):
        runner = DockerIsolatedTestRunner(
            self.root,
            image=IMAGE,
            trusted_principal=lambda principal: principal.actor_id == "docker-integration",
            approve_run=lambda actor, run_id, digest: True,
            audit=self.events.append,
            allowed_test_modules=("tests.test_isolation_contract",),
            timeout_seconds=20,
        )
        # Restore any pre-existing runner environment value after the test;
        # never mutate the trusted runner's process environment permanently.
        with patch.dict(os.environ, {"YUKI_INTEGRATION_SECRET": "must-not-be-forwarded"}):
            result = runner.run(principal=self.principal, test_module="tests.test_isolation_contract")
        self.assertEqual(result.exit_code, 0, msg=result.stdout + "\n" + result.stderr)
        self.assertFalse(result.timed_out)
        self.assertTrue(any(event.get("outcome") == "succeeded" for event in self.events))


if __name__ == "__main__":
    unittest.main()
