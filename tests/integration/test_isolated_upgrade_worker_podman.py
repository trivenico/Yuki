"""Opt-in integration tests against the configured rootless Podman runtime.

Run with YUKI_PODMAN_INTEGRATION=1 and YUKI_TEST_WORKER_IMAGE set to an immutable
sha256 image digest that contains Python 3.11+ and unittest. This test intentionally
does not build or pull images, because image acquisition belongs to trusted setup.
"""
import os
import tempfile
from pathlib import Path
import unittest

from automation.authorized_read_tools import ToolPrincipal
from automation.isolated_upgrade_worker import PodmanIsolatedTestRunner


ENABLED = os.environ.get("YUKI_PODMAN_INTEGRATION") == "1"
IMAGE = os.environ.get("YUKI_TEST_WORKER_IMAGE", "")


@unittest.skipUnless(ENABLED, "set YUKI_PODMAN_INTEGRATION=1 to enable runtime integration")
class RootlessPodmanIntegrationTests(unittest.TestCase):
    def setUp(self):
        if "@sha256:" not in IMAGE:
            self.skipTest("YUKI_TEST_WORKER_IMAGE must be an immutable image digest")
        self.temp = tempfile.TemporaryDirectory(prefix="yuki-podman-it-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "tests").mkdir()
        (self.root / "tests" / "__init__.py").write_text("", encoding="utf-8")
        self.target = self.root / "tests" / "test_isolation_contract.py"
        self.target.write_text(
            "import os, pathlib, socket, tempfile, unittest\n"
            "class IsolationContract(unittest.TestCase):\n"
            " def test_worker_runs_non_root(self):\n"
            "  self.assertNotEqual(os.geteuid(), 0)\n"
            " def test_host_secret_not_forwarded(self):\n"
            "  self.assertNotIn('YUKI_INTEGRATION_SECRET', os.environ)\n"
            " def test_root_filesystem_is_not_writable(self):\n"
            "  with self.assertRaises(OSError): pathlib.Path('/etc/yuki-worker-escape').write_text('no')\n"
            " def test_artifact_mount_is_read_only(self):\n"
            "  with self.assertRaises(OSError): pathlib.Path('/tmp/worker-artifact-escape').write_text('no')\n"
            " def test_bounded_tmpfs_is_writable_for_scratch(self):\n"
            "  with tempfile.NamedTemporaryFile(dir='/work') as scratch: self.assertTrue(pathlib.Path(scratch.name).exists())\n"
            " def test_network_is_unavailable(self):\n"
            "  s=socket.socket(); s.settimeout(1)\n"
            "  try:\n"
            "   with self.assertRaises(OSError): s.connect(('1.1.1.1', 443))\n"
            "  finally: s.close()\n",
            encoding="utf-8",
        )
        self.principal = ToolPrincipal("podman-integration", frozenset({"tests:execute"}))
        self.events = []

    def test_runtime_enforces_basic_isolation_contract(self):
        runner = PodmanIsolatedTestRunner(
            self.root,
            image=IMAGE,
            trusted_principal=lambda principal: principal.actor_id == "podman-integration",
            approve_run=lambda actor, run_id, digest: True,
            audit=self.events.append,
            allowed_test_modules=("tests.test_isolation_contract",),
            timeout_seconds=20,
        )
        os.environ["YUKI_INTEGRATION_SECRET"] = "must-not-be-forwarded"
        self.addCleanup(os.environ.pop, "YUKI_INTEGRATION_SECRET", None)
        result = runner.run(principal=self.principal, test_module="tests.test_isolation_contract")
        self.assertEqual(result.exit_code, 0, msg=result.stdout + "\n" + result.stderr)
        self.assertFalse(result.timed_out)
        self.assertTrue(any(event.get("outcome") == "succeeded" for event in self.events))


if __name__ == "__main__":
    unittest.main()
