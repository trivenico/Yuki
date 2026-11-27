import tempfile
import unittest
from pathlib import Path

from evaluation.cgroup_v2_preflight import inspect_cgroup_v2_parent


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


if __name__ == "__main__":
    unittest.main()
