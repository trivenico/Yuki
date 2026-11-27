"""Tests for descriptor-pinned cgroup-v2 control writes."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from evaluation.cgroup_v2_fd import open_pinned_cgroup_directory
from evaluation.linux_cgroup_v2 import _write_control


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
