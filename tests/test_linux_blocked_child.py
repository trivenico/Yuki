"""Integration tests for the non-executing Linux blocked-child bootstrap."""
from __future__ import annotations

import os
import unittest

try:
    import fcntl
except ImportError:  # pragma: no cover - non-Linux
    fcntl = None

from evaluation.linux_blocked_child import fork_linux_blocked_child


@unittest.skipUnless(
    os.name == "posix"
    and hasattr(os, "fork")
    and hasattr(os, "pidfd_open")
    and hasattr(os, "memfd_create")
    and fcntl is not None
    and hasattr(fcntl, "F_ADD_SEALS"),
    "Linux fork, pidfd, and sealed memfd support is required",
)
class LinuxBlockedChildTests(unittest.TestCase):
    def setUp(self):
        self.artifact = os.memfd_create(
            "yuki-blocked-child-test",
            flags=os.MFD_CLOEXEC | os.MFD_ALLOW_SEALING,
        )
        os.write(self.artifact, b"immutable test artifact")
        required = (
            fcntl.F_SEAL_SEAL
            | fcntl.F_SEAL_SHRINK
            | fcntl.F_SEAL_GROW
            | fcntl.F_SEAL_WRITE
        )
        fcntl.fcntl(self.artifact, fcntl.F_ADD_SEALS, required)

    def tearDown(self):
        os.close(self.artifact)

    def test_child_is_blocked_and_has_only_explicit_descriptors(self):
        with fork_linux_blocked_child(self.artifact) as child:
            self.assertTrue(child.verify_identity())
            self.assertTrue(child.verify_descriptor_allowlist())
            self.assertTrue(child.verify_exec_barrier())
            self.assertFalse(child._released)
            child.release()
            self.assertEqual(child.wait(5), 0)

    def test_release_is_required_before_wait(self):
        with fork_linux_blocked_child(self.artifact) as child:
            with self.assertRaises(RuntimeError):
                child.wait(1)

    def test_unsealed_artifact_is_rejected_before_fork(self):
        unsealed = os.memfd_create("yuki-unsealed-test", flags=os.MFD_CLOEXEC)
        try:
            with self.assertRaises(ValueError):
                fork_linux_blocked_child(unsealed)
        finally:
            os.close(unsealed)

    def test_multithreaded_parent_is_rejected(self):
        import threading

        original = threading.active_count
        try:
            threading.active_count = lambda: 2
            with self.assertRaises(RuntimeError):
                fork_linux_blocked_child(self.artifact)
        finally:
            threading.active_count = original


if __name__ == "__main__":
    unittest.main()
