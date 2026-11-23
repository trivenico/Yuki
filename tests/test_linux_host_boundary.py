import tempfile
import unittest
from pathlib import Path

from core.host_runtime_inventory import RuntimeInventoryError
from core.linux_host_boundary import LinuxHostBoundaryObserver


class LinuxHostBoundaryObserverTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.approved = self.root / "approved"
        self.workspace = self.approved / "task-workspace"
        self.workspace.mkdir(parents=True)
        self.proc = self.root / "proc"
        self.pid_dir = self.proc / "4242"
        self.pid_dir.mkdir(parents=True)
        self.cgroups = self.root / "cgroup"
        self.leaf = self.cgroups / "yuki" / "task"
        self.leaf.mkdir(parents=True)
        (self.pid_dir / "cgroup").write_text("0::/yuki/task\n", encoding="ascii")
        (self.cgroups / "memory.max").write_text(str(1024 * 1024 * 1024), encoding="ascii")
        (self.cgroups / "cpu.max").write_text("200000 100000", encoding="ascii")
        (self.cgroups / "yuki" / "memory.max").write_text(str(512 * 1024 * 1024), encoding="ascii")
        (self.cgroups / "yuki" / "cpu.max").write_text("100000 100000", encoding="ascii")
        (self.leaf / "memory.max").write_text(str(256 * 1024 * 1024), encoding="ascii")
        (self.leaf / "cpu.max").write_text("50000 100000", encoding="ascii")
        self.observer = LinuxHostBoundaryObserver(
            approved_workspace_root=str(self.approved),
            proc_root=str(self.proc), cgroup_root=str(self.cgroups),
        )
        self.inspect = {
            "Id": "a" * 64,
            "State": {"Running": True, "Pid": 4242},
            "Mounts": [{
                "Type": "bind", "Source": str(self.workspace),
                "Destination": "/workspace", "RW": True,
            }],
        }

    def tearDown(self):
        self.temp.cleanup()

    def test_measures_tightest_ancestor_cgroup_limits_and_workspace(self):
        result = self.observer.observe(
            container_inspect=self.inspect, expected_container_id="a" * 64,
        )
        self.assertEqual(result.cpu_millis, 500)
        self.assertEqual(result.memory_mb, 256)
        self.assertEqual(result.workspace_target, "/workspace")
        self.assertTrue(result.workspace_within_approved_root)
        self.assertTrue(result.workspace_symlink_free)
        self.assertTrue(result.workspace_writable)
        self.assertEqual(result.workspace_root_id, result.approved_workspace_root_id)
        self.assertGreater(result.disk_free_mb, 0)

    def test_rejects_wrong_container_identity(self):
        with self.assertRaises(RuntimeInventoryError):
            self.observer.observe(
                container_inspect=self.inspect, expected_container_id="b" * 64,
            )

    def test_rejects_workspace_outside_approved_root(self):
        outside = self.root / "outside"
        outside.mkdir()
        self.inspect["Mounts"][0]["Source"] = str(outside)
        with self.assertRaises(RuntimeInventoryError):
            self.observer.observe(
                container_inspect=self.inspect, expected_container_id="a" * 64,
            )

    def test_rejects_workspace_symlink(self):
        link = self.approved / "linked"
        link.symlink_to(self.workspace, target_is_directory=True)
        self.inspect["Mounts"][0]["Source"] = str(link)
        with self.assertRaises(RuntimeInventoryError):
            self.observer.observe(
                container_inspect=self.inspect, expected_container_id="a" * 64,
            )

    def test_rejects_read_only_workspace_mount(self):
        self.inspect["Mounts"][0]["RW"] = False
        result = self.observer.observe(
            container_inspect=self.inspect, expected_container_id="a" * 64,
        )
        self.assertFalse(result.workspace_writable)

    def test_rejects_missing_finite_cgroup_limits(self):
        (self.leaf / "memory.max").write_text("max", encoding="ascii")
        (self.cgroups / "yuki" / "memory.max").write_text("max", encoding="ascii")
        (self.cgroups / "memory.max").write_text("max", encoding="ascii")
        with self.assertRaises(RuntimeInventoryError):
            self.observer.observe(
                container_inspect=self.inspect, expected_container_id="a" * 64,
            )

    def test_rejects_non_v2_or_malformed_cgroup_path(self):
        (self.pid_dir / "cgroup").write_text("2:cpu:/legacy\n", encoding="ascii")
        with self.assertRaises(RuntimeInventoryError):
            self.observer.observe(
                container_inspect=self.inspect, expected_container_id="a" * 64,
            )


if __name__ == "__main__":
    unittest.main()
