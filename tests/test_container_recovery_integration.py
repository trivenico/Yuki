"""Opt-in Docker-daemon tests for container recovery safety.

Run with YUKI_DOCKER_INTEGRATION=1 after Docker is available and alpine:3.20
has been pulled. Test cleanup force-removes only containers created by this file.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import unittest
import uuid

from core.container_recovery import (
    ContainerRecoveryError,
    container_name_for_execution,
    reconcile_execution_container,
)


_ENABLED = os.environ.get("YUKI_DOCKER_INTEGRATION") == "1" and shutil.which("docker") is not None


@unittest.skipUnless(_ENABLED, "set YUKI_DOCKER_INTEGRATION=1 with a Docker daemon to run integration tests")
class DockerRecoveryIntegrationTests(unittest.TestCase):
    image = "alpine:3.20"

    def setUp(self):
        self.execution_id = "it-" + uuid.uuid4().hex
        self.container_name = container_name_for_execution(self.execution_id)

    def tearDown(self):
        # Only clean up the unique container this test created.
        subprocess.run(
            ["docker", "rm", "-f", self.container_name],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=15,
            check=False,
            shell=False,
        )

    def _create(self, *, managed="true", running=False):
        command = ["docker", "run", "--detach"] if running else ["docker", "create"]
        command += [
            "--name", self.container_name,
            "--label", f"com.yuki.managed={managed}",
            "--label", f"com.yuki.execution-id={self.execution_id}",
            self.image, "sleep", "3600",
        ]
        completed = subprocess.run(
            command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, timeout=30, check=False, shell=False, text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def _inspect_running(self):
        completed = subprocess.run(
            ["docker", "inspect", "--format", "{{.State.Running}}", self.container_name],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=10, check=False, shell=False, text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        return completed.stdout.strip()

    def test_stopped_owned_container_is_removed(self):
        self._create()
        result = reconcile_execution_container(self.execution_id)
        self.assertTrue(result.removed)
        self.assertFalse(result.already_absent)
        check = subprocess.run(
            ["docker", "inspect", self.container_name],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=10, check=False, shell=False,
        )
        self.assertNotEqual(check.returncode, 0)

    def test_running_owned_container_is_not_killed(self):
        self._create(running=True)
        with self.assertRaisesRegex(ContainerRecoveryError, "still running"):
            reconcile_execution_container(self.execution_id)
        self.assertEqual(self._inspect_running(), "true")

    def test_wrong_owner_label_refuses_removal(self):
        self._create(managed="false")
        with self.assertRaisesRegex(ContainerRecoveryError, "ownership labels"):
            reconcile_execution_container(self.execution_id)
        self.assertEqual(self._inspect_running(), "false")


if __name__ == "__main__":
    unittest.main()
