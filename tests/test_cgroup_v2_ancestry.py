import os
import tempfile
import unittest
from pathlib import Path

from evaluation.cgroup_v2_ancestry import (
    verify_cgroup_v2_ancestry,
    verify_cgroup_v2_ancestry_pinned,
)
from evaluation.runner_isolation_policy import create_runner_isolation_policy


class CgroupV2AncestryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "cg"
        self.child = self.root / "delegated" / "yuki-runner"
        self.child.mkdir(parents=True)
        self.policy = create_runner_isolation_policy(
            uid=65532, gid=65532, seccomp_profile_digest="c" * 64
        )
        self.parent_limits = {
            "cpu.max": "200000 100000",
            "memory.max": str(1024 * 1024**2),
            "memory.swap.max": "0",
            "pids.max": "32",
        }
        self._write_limits(self.root, self.parent_limits)
        self._write_limits(self.root / "delegated", self.parent_limits)
        self._write_limits(self.child, self.policy.cgroup_v2_settings())

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def _write_limits(directory, limits):
        directory.mkdir(parents=True, exist_ok=True)
        for name, value in limits.items():
            (directory / name).write_text(value + "\n")

    def test_verifies_child_and_every_ancestor_including_mount_root(self):
        result = verify_cgroup_v2_ancestry(
            self.policy, mount_root=self.root, child_path=self.child
        )
        self.assertTrue(result.verified)
        self.assertEqual(result.reason, "configured_ceilings_verified")
        self.assertEqual(
            result.inspected_ancestors,
            (str(self.root / "delegated"), str(self.root)),
        )

    def test_fails_when_intermediate_ancestor_is_too_small(self):
        limits = dict(self.parent_limits)
        limits["memory.max"] = str(128 * 1024**2)
        self._write_limits(self.root / "delegated", limits)
        result = verify_cgroup_v2_ancestry(
            self.policy, mount_root=self.root, child_path=self.child
        )
        self.assertFalse(result.verified)
        self.assertEqual(result.failed_ancestors, (str(self.root / "delegated"),))

    def test_fails_when_mount_root_ceiling_is_too_small(self):
        limits = dict(self.parent_limits)
        limits["pids.max"] = "4"
        self._write_limits(self.root, limits)
        result = verify_cgroup_v2_ancestry(
            self.policy, mount_root=self.root, child_path=self.child
        )
        self.assertFalse(result.verified)
        self.assertEqual(result.failed_ancestors, (str(self.root),))

    def test_fails_closed_on_unreadable_ancestor(self):
        (self.root / "delegated" / "cpu.max").unlink()
        result = verify_cgroup_v2_ancestry(
            self.policy, mount_root=self.root, child_path=self.child
        )
        self.assertFalse(result.verified)
        self.assertEqual(result.failed_ancestors, (str(self.root / "delegated"),))

    def test_fails_when_child_readback_does_not_match_policy(self):
        (self.child / "pids.max").write_text("64\n")
        result = verify_cgroup_v2_ancestry(
            self.policy, mount_root=self.root, child_path=self.child
        )
        self.assertFalse(result.verified)
        self.assertEqual(result.reason, "child_limits_mismatch")

    def test_rejects_child_outside_mount_root(self):
        outside = Path(self.temp.name) / "outside"
        self._write_limits(outside, self.policy.cgroup_v2_settings())
        result = verify_cgroup_v2_ancestry(
            self.policy, mount_root=self.root, child_path=outside
        )
        self.assertFalse(result.verified)
        self.assertEqual(result.reason, "unavailable_or_malformed")

    def test_pinned_descriptor_walk_verifies_child_and_all_ancestors(self):
        if not Path("/proc/self/fdinfo").is_dir():
            self.skipTest("Linux procfs fdinfo is required")
        root_fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
        try:
            result = verify_cgroup_v2_ancestry_pinned(
                self.policy,
                trusted_mount_root_fd=root_fd,
                child_relative_path="delegated/yuki-runner",
                mount_root_label="test-cgroup-root",
            )
        finally:
            os.close(root_fd)
        self.assertTrue(result.verified)
        self.assertEqual(
            result.inspected_ancestors,
            ("test-cgroup-root/delegated", "test-cgroup-root"),
        )

    def test_pinned_descriptor_walk_rejects_too_small_ancestor_and_keeps_caller_fd(self):
        if not Path("/proc/self/fdinfo").is_dir():
            self.skipTest("Linux procfs fdinfo is required")
        limits = dict(self.parent_limits)
        limits["memory.max"] = str(128 * 1024**2)
        self._write_limits(self.root / "delegated", limits)
        root_fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
        try:
            result = verify_cgroup_v2_ancestry_pinned(
                self.policy,
                trusted_mount_root_fd=root_fd,
                child_relative_path="delegated/yuki-runner",
            )
            os.fstat(root_fd)
        finally:
            os.close(root_fd)
        self.assertFalse(result.verified)
        self.assertEqual(
            result.failed_ancestors,
            ("<pinned-cgroup-mount>/delegated",),
        )

    def test_pinned_walk_never_reuses_caller_fd_after_initial_pin(self):
        if not Path("/proc/self/fdinfo").is_dir():
            self.skipTest("Linux procfs fdinfo is required")
        from unittest.mock import patch
        decoy = Path(self.temp.name) / "decoy"
        self._write_limits(decoy, {
            **self.parent_limits,
            "memory.max": str(128 * 1024**2),
        })
        root_fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
        original_read = __import__(
            "evaluation.cgroup_v2_ancestry", fromlist=["_read_pinned_limits"]
        )._read_pinned_limits
        swapped = False

        def swap_caller_fd(directory):
            nonlocal swapped
            result = original_read(directory)
            if not swapped:
                swapped = True
                replacement = os.open(decoy, os.O_RDONLY | os.O_DIRECTORY)
                os.close(root_fd)
                os.dup2(replacement, root_fd)
                os.close(replacement)
            return result

        try:
            with patch(
                "evaluation.cgroup_v2_ancestry._read_pinned_limits",
                side_effect=swap_caller_fd,
            ):
                result = verify_cgroup_v2_ancestry_pinned(
                    self.policy,
                    trusted_mount_root_fd=root_fd,
                    child_relative_path="delegated/yuki-runner",
                )
        finally:
            os.close(root_fd)
        self.assertTrue(result.verified)
        self.assertEqual(result.reason, "configured_ceilings_verified")

    def test_pinned_descriptor_walk_rejects_symlink_component(self):
        if not Path("/proc/self/fdinfo").is_dir():
            self.skipTest("Linux procfs fdinfo is required")
        target = self.root / "delegated"
        moved = self.root / "delegated-real"
        target.rename(moved)
        target.symlink_to(moved, target_is_directory=True)
        root_fd = __import__("os").open(self.root, __import__("os").O_RDONLY | __import__("os").O_DIRECTORY)
        try:
            result = verify_cgroup_v2_ancestry_pinned(
                self.policy,
                trusted_mount_root_fd=root_fd,
                child_relative_path="delegated/yuki-runner",
            )
        finally:
            __import__("os").close(root_fd)
        self.assertFalse(result.verified)
        self.assertEqual(result.reason, "unavailable_or_malformed")

    def test_rejects_mount_root_as_child(self):
        result = verify_cgroup_v2_ancestry(
            self.policy, mount_root=self.root, child_path=self.root
        )
        self.assertFalse(result.verified)
        self.assertEqual(result.reason, "unavailable_or_malformed")


if __name__ == "__main__":
    unittest.main()
