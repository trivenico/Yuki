import tempfile
import unittest
from pathlib import Path

from evaluation.cgroup_v2_preflight import (
    inspect_cgroup_v2_parent,
    verify_parent_resource_ceiling,
)
from evaluation.runner_isolation_policy import create_runner_isolation_policy


class CgroupV2PreflightTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "cgroup.controllers").write_text("cpu memory pids\n")
        (self.root / "cgroup.subtree_control").write_text("cpu memory pids\n")
        (self.root / "cgroup.procs").write_text("")

    def tearDown(self):
        self.temp.cleanup()

    def writable(self, _path, _mode):
        return True

    def test_ready_when_required_controllers_are_available_and_enabled(self):
        result = inspect_cgroup_v2_parent(self.root, access_check=self.writable)
        self.assertTrue(result.ready)
        self.assertEqual(result.available_controllers, ("cpu", "memory", "pids"))

    def test_missing_controller_fails_closed(self):
        (self.root / "cgroup.controllers").write_text("cpu memory\n")
        result = inspect_cgroup_v2_parent(self.root, access_check=self.writable)
        self.assertFalse(result.ready)
        self.assertEqual(result.missing_controllers, ("pids",))
        self.assertEqual(result.reason, "missing_controllers")

    def test_controller_not_enabled_in_parent_fails_closed(self):
        (self.root / "cgroup.subtree_control").write_text("cpu memory\n")
        result = inspect_cgroup_v2_parent(self.root, access_check=self.writable)
        self.assertFalse(result.ready)
        self.assertEqual(result.disabled_controllers, ("pids",))
        self.assertEqual(result.reason, "controllers_not_delegated")

    def test_non_writable_parent_fails_closed(self):
        result = inspect_cgroup_v2_parent(
            self.root, access_check=lambda _path, _mode: False
        )
        self.assertFalse(result.ready)
        self.assertEqual(result.reason, "parent_not_writable")

    def test_missing_control_file_fails_closed(self):
        (self.root / "cgroup.procs").unlink()
        result = inspect_cgroup_v2_parent(self.root, access_check=self.writable)
        self.assertFalse(result.available)
        self.assertEqual(result.reason, "unavailable_or_malformed")

    def test_malformed_and_duplicate_controller_tokens_fail_closed(self):
        (self.root / "cgroup.controllers").write_text("cpu cpu memory pids\n")
        result = inspect_cgroup_v2_parent(self.root, access_check=self.writable)
        self.assertFalse(result.ready)
        self.assertEqual(result.reason, "unavailable_or_malformed")

    def test_invalid_required_controller_input_fails_closed(self):
        result = inspect_cgroup_v2_parent(
            self.root, required_controllers=frozenset({"cpu", "../memory"}),
            access_check=self.writable,
        )
        self.assertFalse(result.ready)
        self.assertEqual(result.reason, "unavailable_or_malformed")

    def test_preflight_does_not_modify_control_files(self):
        before = {
            name: (self.root / name).read_bytes()
            for name in ("cgroup.controllers", "cgroup.subtree_control", "cgroup.procs")
        }
        inspect_cgroup_v2_parent(self.root, access_check=self.writable)
        after = {
            name: (self.root / name).read_bytes()
            for name in before
        }
        self.assertEqual(after, before)


class ParentResourceCeilingTests(unittest.TestCase):
    def setUp(self):
        self.policy = create_runner_isolation_policy(
            uid=65532, gid=65532, seccomp_profile_digest="b" * 64
        )
        self.limits = {
            "cpu.max": "200000 100000",
            "memory.max": str(1024 * 1024**2),
            "memory.swap.max": "0",
            "pids.max": "32",
        }

    def test_parent_with_sufficient_limits_is_accepted(self):
        self.assertTrue(verify_parent_resource_ceiling(self.policy, self.limits))

    def test_parent_cpu_quota_is_compared_as_a_ratio(self):
        limits = dict(self.limits)
        limits["cpu.max"] = "500000 1000000"
        self.assertTrue(verify_parent_resource_ceiling(self.policy, limits))
        limits["cpu.max"] = "500000 2000000"
        self.assertFalse(verify_parent_resource_ceiling(self.policy, limits))

    def test_parent_memory_and_pid_ceilings_must_fit(self):
        limits = dict(self.limits)
        limits["memory.max"] = str(128 * 1024**2)
        self.assertFalse(verify_parent_resource_ceiling(self.policy, limits))
        limits = dict(self.limits)
        limits["pids.max"] = "8"
        self.assertFalse(verify_parent_resource_ceiling(self.policy, limits))

    def test_unlimited_parent_ceilings_are_supported(self):
        limits = {
            "cpu.max": "max 100000",
            "memory.max": "max",
            "memory.swap.max": "max",
            "pids.max": "max",
        }
        self.assertTrue(verify_parent_resource_ceiling(self.policy, limits))

    def test_missing_or_malformed_parent_limits_fail_closed(self):
        self.assertFalse(verify_parent_resource_ceiling(
            self.policy, {key: value for key, value in self.limits.items()
                          if key != "pids.max"}
        ))
        for malformed in ("max", "100000", "0 100000", "100000 0", "01 100000"):
            limits = dict(self.limits)
            limits["cpu.max"] = malformed
            self.assertFalse(verify_parent_resource_ceiling(self.policy, limits))

    def test_tampered_policy_fails_closed(self):
        from dataclasses import replace
        tampered = replace(self.policy, memory_limit_bytes=4 * 1024**3)
        self.assertFalse(verify_parent_resource_ceiling(tampered, self.limits))


if __name__ == "__main__":
    unittest.main()
