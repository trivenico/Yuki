"""Tests for pidfd-backed Linux process identity snapshots."""
from __future__ import annotations

import os
import subprocess
import sys
import time
import unittest

from evaluation.linux_process_identity import PinnedLinuxProcess


@unittest.skipUnless(
    sys.platform.startswith("linux") and hasattr(os, "pidfd_open"),
    "Linux pidfd_open is required",
)
class PinnedLinuxProcessTests(unittest.TestCase):
    def test_current_process_identity_is_stable(self):
        with PinnedLinuxProcess.open(os.getpid()) as process:
            self.assertGreater(process.start_time_ticks, 0)
            self.assertTrue(process.verify())

    def test_exited_process_is_not_reported_as_live(self):
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        try:
            with PinnedLinuxProcess.open(child.pid) as process:
                child.terminate()
                child.wait(timeout=5)
                self.assertFalse(process.verify())
        finally:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=5)

    def test_closed_pidfd_cannot_be_reused(self):
        process = PinnedLinuxProcess.open(os.getpid())
        process.close()
        with self.assertRaises(ValueError):
            process.verify()

    def test_invalid_pid_is_rejected(self):
        for pid in (0, -1, True, "123"):
            with self.subTest(pid=pid):
                with self.assertRaises(ValueError):
                    PinnedLinuxProcess.open(pid)


if __name__ == "__main__":
    unittest.main()
