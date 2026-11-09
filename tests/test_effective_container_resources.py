import tempfile
import unittest
import sys
from pathlib import Path

from core.device_agent_client import (
    _clamp_cpu_quota,
    _effective_cpu_cores,
    _effective_cpu_millicores,
    _effective_linux_memory,
    _linux_cgroup_paths,
)


class EffectiveContainerResourceTests(unittest.TestCase):
    def test_cgroup_v2_memory_clamps_host_total_and_available(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "memory.max").write_text(str(2 * 1024**3))
            (root / "memory.current").write_text(str(512 * 1024**2))
            total, available = _effective_linux_memory(
                16 * 1024**3, 12 * 1024**3, paths={"v2": root}
            )
        self.assertEqual(total, 2 * 1024**3)
        self.assertEqual(available, 2 * 1024**3 - 512 * 1024**2)

    def test_unknown_cgroup_usage_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "memory.max").write_text(str(2 * 1024**3))
            total, available = _effective_linux_memory(
                16 * 1024**3, 12 * 1024**3, paths={"v2": root}
            )
        self.assertEqual(total, 2 * 1024**3)
        self.assertEqual(available, 0)

    def test_unlimited_or_missing_cgroup_limits_preserve_host_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "memory.max").write_text("max")
            total, available = _effective_linux_memory(
                8 * 1024**3, 3 * 1024**3, paths={"v2": root}
            )
        self.assertEqual((total, available), (8 * 1024**3, 3 * 1024**3))

    def test_v1_memory_controller_is_supported(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "memory.limit_in_bytes").write_text(str(1024**3))
            (root / "memory.usage_in_bytes").write_text(str(256 * 1024**2))
            total, available = _effective_linux_memory(
                8 * 1024**3, 6 * 1024**3, paths={"memory": root}
            )
        self.assertEqual(total, 1024**3)
        self.assertEqual(available, 1024**3 - 256 * 1024**2)

    def test_v2_cpu_quota_caps_affinity(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "cpu.max").write_text("150000 100000")
            self.assertEqual(_clamp_cpu_quota(8, paths={"v2": root}), 1)

    def test_unlimited_cpu_quota_preserves_affinity(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "cpu.max").write_text("max 100000")
            self.assertEqual(_clamp_cpu_quota(6, paths={"v2": root}), 6)

    def test_cgroup_path_resolution_reads_unified_membership(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            membership = root / "cgroup"
            membership.write_text("0::/user.slice/yuki.scope\n")
            paths = _linux_cgroup_paths(proc_cgroup=membership, cgroup_root=root / "unified")
        self.assertEqual(paths["v2"], root / "unified/user.slice/yuki.scope")

    def test_fractional_cpu_quota_is_reported_in_millicores(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "cpu.max").write_text("50000 100000")
            self.assertEqual(_effective_cpu_millicores(affinity=8, paths={"v2": root}), 500)

    def test_cpu_quota_preserves_quarter_core_capacity(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "cpu.max").write_text("25000 100000")
            self.assertEqual(_effective_cpu_millicores(affinity=8, paths={"v2": root}), 250)

    def test_cpu_quota_preserves_one_and_a_half_cores(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "cpu.max").write_text("150000 100000")
            self.assertEqual(_effective_cpu_millicores(affinity=8, paths={"v2": root}), 1500)

    @unittest.skipUnless(sys.platform.startswith("linux"), "mountinfo is a Linux kernel interface")
    def test_mountinfo_resolves_non_root_cgroup2_mount(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            mount = root / "cgroup2"
            mount.mkdir()
            membership = root / "cgroup"
            membership.write_text("0::/tenant/user.slice/yuki.scope\n")
            mountinfo = root / "mountinfo"
            mountinfo.write_text(f"36 25 0:32 /tenant {mount} rw,nosuid - cgroup2 cgroup rw\n")
            paths = _linux_cgroup_paths(
                proc_cgroup=membership, cgroup_root=mount, mountinfo=mountinfo
            )
        self.assertEqual(paths["v2"], (mount / "user.slice/yuki.scope").resolve())

    @unittest.skipUnless(sys.platform.startswith("linux"), "mountinfo is a Linux kernel interface")
    def test_mountinfo_resolves_v1_controller_mount(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            mount = root / "memory"
            mount.mkdir()
            membership = root / "cgroup"
            membership.write_text("5:memory:/docker/container123\n")
            mountinfo = root / "mountinfo"
            mountinfo.write_text(f"37 25 0:33 / {mount} rw - cgroup cgroup rw,memory\n")
            paths = _linux_cgroup_paths(
                proc_cgroup=membership, cgroup_root=root, mountinfo=mountinfo
            )
        self.assertEqual(paths["memory"], (mount / "docker/container123").resolve())

    @unittest.skipUnless(sys.platform.startswith("linux"), "mountinfo is a Linux kernel interface")
    def test_mountinfo_rejects_membership_outside_hierarchy_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            mount = root / "cgroup2"
            mount.mkdir()
            membership = root / "cgroup"
            membership.write_text("0::/outside/escape\n")
            mountinfo = root / "mountinfo"
            mountinfo.write_text(f"36 25 0:32 /tenant {mount} rw - cgroup2 cgroup rw\n")
            paths = _linux_cgroup_paths(
                proc_cgroup=membership, cgroup_root=mount, mountinfo=mountinfo
            )
        self.assertNotIn("v2", paths)


if __name__ == "__main__":
    unittest.main()
