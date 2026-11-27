import os
import tempfile
import unittest
from pathlib import Path

from evaluation.cgroup_v2_fd import (
    open_pinned_cgroup_directory,
    pinned_cgroup_contains_pid,
    read_pinned_cgroup_control,
)


class PinnedCgroupDirectoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.child = self.root / "delegated" / "attempt-1"
        self.child.mkdir(parents=True)
        (self.child / "cgroup.procs").write_text("1234\n")
        (self.child / "cpu.max").write_text("100000 100000\n")
        self.root_fd = os.open(
            self.root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        )

    def tearDown(self):
        os.close(self.root_fd)
        self.temp.cleanup()

    def test_opens_descendant_and_reads_controls_through_pinned_fd(self):
        with open_pinned_cgroup_directory(
            self.root_fd, "delegated/attempt-1"
        ) as pinned:
            self.assertEqual(
                read_pinned_cgroup_control(pinned, "cpu.max"), "100000 100000"
            )
            self.assertTrue(pinned_cgroup_contains_pid(pinned, 1234))
            self.assertFalse(pinned_cgroup_contains_pid(pinned, 1235))

    def test_rejects_absolute_traversal_empty_and_noncanonical_paths(self):
        for path in ("", "/delegated/attempt-1", "../escape",
                     "delegated/../escape", "delegated//attempt-1", "./child"):
            with self.subTest(path=path):
                with self.assertRaises(ValueError):
                    open_pinned_cgroup_directory(self.root_fd, path)

    def test_rejects_symlink_component(self):
        outside = self.root / "outside"
        outside.mkdir()
        (self.root / "link").symlink_to(outside, target_is_directory=True)
        with self.assertRaises(OSError):
            open_pinned_cgroup_directory(self.root_fd, "link")

    def test_rejects_symlink_control_file(self):
        (self.child / "cpu.max").unlink()
        outside = self.root / "outside-control"
        outside.write_text("100000 100000")
        (self.child / "cpu.max").symlink_to(outside)
        with open_pinned_cgroup_directory(self.root_fd, "delegated/attempt-1") as pinned:
            with self.assertRaises(OSError):
                read_pinned_cgroup_control(pinned, "cpu.max")

    def test_rejects_unallowlisted_control_file(self):
        with open_pinned_cgroup_directory(self.root_fd, "delegated/attempt-1") as pinned:
            with self.assertRaises(ValueError):
                read_pinned_cgroup_control(pinned, "../etc/passwd")

    def test_rejects_invalid_pid_and_malformed_membership(self):
        with open_pinned_cgroup_directory(self.root_fd, "delegated/attempt-1") as pinned:
            for pid in (True, 0, -1, "1234"):
                with self.assertRaises(ValueError):
                    pinned_cgroup_contains_pid(pinned, pid)
            (self.child / "cgroup.procs").write_text("not-a-pid")
            with self.assertRaises(ValueError):
                pinned_cgroup_contains_pid(pinned, 1234)

    def test_pinned_descriptor_remains_bound_after_directory_rename(self):
        pinned = open_pinned_cgroup_directory(
            self.root_fd, "delegated/attempt-1"
        )
        moved = self.root / "delegated" / "renamed"
        self.child.rename(moved)
        replacement = self.root / "delegated" / "attempt-1"
        replacement.mkdir()
        (replacement / "cpu.max").write_text("max 100000")
        try:
            self.assertEqual(
                read_pinned_cgroup_control(pinned, "cpu.max"), "100000 100000"
            )
            with open_pinned_cgroup_directory(
                self.root_fd, "delegated/attempt-1"
            ) as replacement_handle:
                self.assertEqual(
                    read_pinned_cgroup_control(replacement_handle, "cpu.max"),
                    "max 100000",
                )
        finally:
            pinned.close()

    def test_rejects_closed_pinned_descriptor(self):
        pinned = open_pinned_cgroup_directory(self.root_fd, "delegated/attempt-1")
        pinned.close()
        with self.assertRaises(ValueError):
            read_pinned_cgroup_control(pinned, "cpu.max")

    def test_rejects_non_directory_root_descriptor(self):
        file_path = self.root / "not-a-directory"
        file_path.write_text("x")
        fd = os.open(file_path, os.O_RDONLY)
        try:
            with self.assertRaises(ValueError):
                open_pinned_cgroup_directory(fd, "child")
        finally:
            os.close(fd)


if __name__ == "__main__":
    unittest.main()
