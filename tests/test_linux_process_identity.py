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

    def test_child_descriptor_allowlist_matches_actual_procfs_snapshot(self):
        read_fd, write_fd = os.pipe()
        child = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            pass_fds=(write_fd,),
        )
        os.close(write_fd)
        try:
            with PinnedLinuxProcess.open(child.pid) as process:
                expected = (0, 1, 2, write_fd)
                observed = process.open_file_descriptors()
                self.assertIn(write_fd, observed)
                self.assertTrue(process.verify_descriptor_allowlist(observed))
                self.assertFalse(process.verify_descriptor_allowlist(expected))
        finally:
            os.close(read_fd)
            if child.poll() is None:
                child.terminate()
                child.wait(timeout=5)

    def test_descriptor_allowlist_rejects_duplicate_or_invalid_values(self):
        with PinnedLinuxProcess.open(os.getpid()) as process:
            with self.assertRaises(ValueError):
                process.verify_descriptor_allowlist((0, 0))
            with self.assertRaises(ValueError):
                process.verify_descriptor_allowlist((-1,))

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
