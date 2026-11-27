"""Tests for descriptor-pinned cgroup-v2 control writes."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from evaluation.cgroup_v2_fd import open_pinned_cgroup_directory
from evaluation.linux_cgroup_v2 import (
    LinuxCgroupV2BlockedAttempt,
    _write_control,
)


class _FakeChild:
    pid = 1234

    def __init__(self):
        self.identity = True
        self.descriptors = True
        self.barrier = True
        self.released = False

    def verify_identity(self):
        return self.identity

    def verify_descriptor_allowlist(self):
        return self.descriptors

    def verify_exec_barrier(self):
        return self.barrier

    def release(self):
        self.released = True


class _FakeCgroup:
    def __init__(self):
        self.membership = True

    def verify_pid(self, pid):
        return self.membership and pid == 1234


class LinuxCgroupV2PreReleaseGateTests(unittest.TestCase):
    def test_release_rechecks_all_gates_before_opening_child_gate(self):
        child = _FakeChild()
        attempt = LinuxCgroupV2BlockedAttempt(_FakeCgroup(), child)
        self.assertTrue(attempt.verify_pre_release())
        attempt.release()
        self.assertTrue(child.released)

    def test_each_failed_gate_blocks_release(self):
        for attribute in ("identity", "descriptors", "barrier"):
            with self.subTest(attribute=attribute):
                child = _FakeChild()
                setattr(child, attribute, False)
                attempt = LinuxCgroupV2BlockedAttempt(_FakeCgroup(), child)
                with self.assertRaisesRegex(RuntimeError, "pre-release"):
                    attempt.release()
                self.assertFalse(child.released)

    def test_missing_cgroup_membership_blocks_release(self):
        child = _FakeChild()
        cgroup = _FakeCgroup()
        cgroup.membership = False
        attempt = LinuxCgroupV2BlockedAttempt(cgroup, child)
        with self.assertRaisesRegex(RuntimeError, "pre-release"):
            attempt.release()
        self.assertFalse(child.released)


class LinuxCgroupV2ControlWriteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.child = self.root / "attempt"
        self.child.mkdir()
        for name in ("cpu.max", "memory.max", "memory.swap.max", "pids.max", "cgroup.procs"):
            (self.child / name).write_text("")
        self.root_fd = os.open(
            self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC
        )
        self.pinned = open_pinned_cgroup_directory(self.root_fd, "attempt")

    def tearDown(self):
        self.pinned.close()
        os.close(self.root_fd)
        self.temp.cleanup()

    def test_writes_only_allowlisted_control_relative_to_pinned_directory(self):
        _write_control(self.pinned, "cpu.max", "50000 100000")
        self.assertEqual((self.child / "cpu.max").read_text(), "50000 100000")

    def test_rejects_unallowlisted_control_and_invalid_value(self):
        with self.assertRaises(ValueError):
            _write_control(self.pinned, "../escape", "1")
        with self.assertRaises(ValueError):
            _write_control(self.pinned, "cpu.max", "1\n2")
        with self.assertRaises(ValueError):
            _write_control(self.pinned, "cpu.max", "x" * 129)

    def test_rejects_symlink_control(self):
        (self.child / "cpu.max").unlink()
        outside = self.root / "outside"
        outside.write_text("unchanged")
        (self.child / "cpu.max").symlink_to(outside)
        with self.assertRaises(OSError):
            _write_control(self.pinned, "cpu.max", "100000 100000")
        self.assertEqual(outside.read_text(), "unchanged")


if __name__ == "__main__":
    unittest.main()
