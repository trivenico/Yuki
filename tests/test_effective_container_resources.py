import tempfile
import unittest
from pathlib import Path

from core.device_agent_client import (
    _effective_cpu_cores,
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

    def test_cgroup_path_resolution_reads_unified_membership(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            membership = root / "cgroup"
            membership.write_text("0::/user.slice/yuki.scope\n")
            paths = _linux_cgroup_paths(proc_cgroup=membership, cgroup_root=root / "unified")
        self.assertEqual(paths["v2"], root / "unified/user.slice/yuki.scope")


if __name__ == "__main__":
    unittest.main()
