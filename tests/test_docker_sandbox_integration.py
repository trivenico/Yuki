"""Opt-in integration tests requiring a real local Docker daemon.

Run with YUKI_DOCKER_INTEGRATION_IMAGE set to an allow-listed digest-pinned
image that is already present locally. Unit-test jobs skip this module otherwise.
"""
import os
import tempfile
import unittest
from pathlib import Path

from hackback.sandbox import DockerSandbox, SandboxError


IMAGE = os.environ.get("YUKI_DOCKER_INTEGRATION_IMAGE")


@unittest.skipUnless(IMAGE, "set YUKI_DOCKER_INTEGRATION_IMAGE to run Docker integration tests")
class DockerSandboxIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.workspace = Path(self.tempdir.name)
        self.sandbox = DockerSandbox(
            self.workspace,
            allowed_images=[IMAGE],
            enabled=True,
            approver=lambda _reason: True,
        )

    def tearDown(self):
        self.tempdir.cleanup()

    def test_workspace_is_actually_read_only(self):
        result = self.sandbox.run(
            IMAGE,
            ["python", "-c", "from pathlib import Path; Path('/workspace/escape').write_text('x')"],
        )
        self.assertNotEqual(result["returncode"], 0)
        self.assertFalse((self.workspace / "escape").exists())

    def test_output_flood_is_terminated_and_rejected(self):
        with self.assertRaisesRegex(SandboxError, "output exceeded"):
            self.sandbox.run(
                IMAGE,
                ["python", "-c", "import sys; sys.stdout.write('x' * 1000000); sys.stdout.flush()"],
            )

    def test_timeout_is_enforced(self):
        with self.assertRaisesRegex(SandboxError, "timed out"):
            self.sandbox.run(IMAGE, ["python", "-c", "import time; time.sleep(10)"], timeout=1)

    def test_network_is_disabled(self):
        # Network isolation is checked at the container namespace level. A raw
        # socket connect to a public resolver must fail or time out quickly.
        result = self.sandbox.run(
            IMAGE,
            [
                "python",
                "-c",
                "import socket; s=socket.socket(); s.settimeout(1); "
                "exec(\\"try: s.connect(('1.1.1.1', 53)); print('CONNECTED')\\n"
                "except OSError: print('BLOCKED')\\")",
            ],
        )
        self.assertEqual(result["returncode"], 0, result["stderr"])
        self.assertIn("BLOCKED", result["stdout"])


if __name__ == "__main__":
    unittest.main()
