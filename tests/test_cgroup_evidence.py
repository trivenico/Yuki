import tempfile
import unittest
from pathlib import Path

from core.cgroup_evidence import inspect_cgroup_v2_limits
from core.sandbox_runtime import SandboxRuntimeError


def make_cgroup(root: Path, membership: str = "/yuki/task-1"):
    (root / "cgroup.controllers").write_text("cpu memory pids\n", encoding="ascii")
    leaf = root / membership.lstrip("/")
    leaf.mkdir(parents=True)
    (leaf / "cpu.max").write_text("50000 100000\n", encoding="ascii")
    (leaf / "memory.max").write_text("536870912\n", encoding="ascii")
    (leaf / "memory.swap.max").write_text("0\n", encoding="ascii")
    (leaf / "pids.max").write_text("64\n", encoding="ascii")
    return leaf


class CgroupV2EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        make_cgroup(self.root)

    def test_reads_effective_leaf_limits_without_attesting(self):
        result = inspect_cgroup_v2_limits("/yuki/task-1", cgroup_root=str(self.root))
        self.assertEqual(result.available_controllers, ("cpu", "memory", "pids"))
        self.assertEqual(result.cpu_quota, 50000)
        self.assertEqual(result.cpu_period, 100000)
        self.assertEqual(result.memory_max_bytes, 536870912)
        self.assertEqual(result.memory_swap_max_bytes, 0)
        self.assertEqual(result.pids_max, 64)
        self.assertFalse(result.attested)
        self.assertTrue(result.unknown_controls)

    def test_preserves_unlimited_values_as_unknown(self):
        leaf = self.root / "yuki" / "task-1"
        (leaf / "cpu.max").write_text("max 100000\n", encoding="ascii")
        (leaf / "memory.max").write_text("max\n", encoding="ascii")
        (leaf / "memory.swap.max").write_text("max\n", encoding="ascii")
        (leaf / "pids.max").write_text("max\n", encoding="ascii")
        result = inspect_cgroup_v2_limits("/yuki/task-1", cgroup_root=str(self.root))
        self.assertIsNone(result.cpu_quota)
        self.assertIsNone(result.memory_max_bytes)
        self.assertIsNone(result.memory_swap_max_bytes)
        self.assertIsNone(result.pids_max)

    def test_rejects_relative_or_noncanonical_membership(self):
        for membership in ("relative", "/yuki/../host", "/yuki//task-1"):
            with self.subTest(membership=membership):
                with self.assertRaises(SandboxRuntimeError):
                    inspect_cgroup_v2_limits(membership, cgroup_root=str(self.root))

    def test_rejects_symlinked_cgroup_directory(self):
        alias = self.root / "yuki" / "alias"
        alias.symlink_to(self.root / "yuki" / "task-1", target_is_directory=True)
        with self.assertRaises(SandboxRuntimeError):
            inspect_cgroup_v2_limits("/yuki/alias", cgroup_root=str(self.root))

    def test_rejects_symlinked_control_file(self):
        leaf = self.root / "yuki" / "task-1"
        original = leaf / "memory.max"
        original.unlink()
        target = self.root / "memory-limit"
        target.write_text("1234\n", encoding="ascii")
        original.symlink_to(target)
        with self.assertRaises(SandboxRuntimeError):
            inspect_cgroup_v2_limits("/yuki/task-1", cgroup_root=str(self.root))

    def test_rejects_malformed_cpu_and_limit_values(self):
        leaf = self.root / "yuki" / "task-1"
        (leaf / "cpu.max").write_text("50000 0\n", encoding="ascii")
        with self.assertRaises(SandboxRuntimeError):
            inspect_cgroup_v2_limits("/yuki/task-1", cgroup_root=str(self.root))
        (leaf / "cpu.max").write_text("50000 100000\n", encoding="ascii")
        (leaf / "pids.max").write_text("-1\n", encoding="ascii")
        with self.assertRaises(SandboxRuntimeError):
            inspect_cgroup_v2_limits("/yuki/task-1", cgroup_root=str(self.root))

    def test_rejects_malformed_controller_list(self):
        (self.root / "cgroup.controllers").write_text("cpu cpu memory\n", encoding="ascii")
        with self.assertRaises(SandboxRuntimeError):
            inspect_cgroup_v2_limits("/yuki/task-1", cgroup_root=str(self.root))

    def test_rejects_symlinked_root(self):
        alias = self.root.parent / (self.root.name + "-cgroup-link")
        alias.symlink_to(self.root, target_is_directory=True)
        self.addCleanup(alias.unlink)
        with self.assertRaises(SandboxRuntimeError):
            inspect_cgroup_v2_limits("/yuki/task-1", cgroup_root=str(alias))


if __name__ == "__main__":
    unittest.main()
