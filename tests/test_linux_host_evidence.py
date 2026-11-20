import tempfile
import unittest
from pathlib import Path

from core.linux_host_evidence import collect_linux_process_snapshot
from core.sandbox_runtime import SandboxRuntimeError


def make_proc(root: Path, pid: int = 4242):
    process = root / str(pid)
    (process / "ns").mkdir(parents=True)
    # state is field 3; starttime is field 22 (index 19 after comm).
    suffix = "S " + " ".join(["0"] * 18 + ["987654"])
    (process / "stat").write_text(f"{pid} (worker ) name) {suffix}\n", encoding="ascii")
    (process / "status").write_text(
        "Name:\tworker\n"
        "Uid:\t1000\t1000\t1000\t1000\n"
        "Gid:\t1001\t1001\t1001\t1001\n"
        "NoNewPrivs:\t1\n"
        "Seccomp:\t2\n"
        "CapEff:\t0000000000000000\n",
        encoding="ascii",
    )
    for name, inode in (("user", 11), ("pid", 12), ("mnt", 13), ("ipc", 14), ("net", 15)):
        (process / "ns" / name).symlink_to(f"{name}:[{inode}]")
    (process / "cgroup").write_text("0::/yuki/tasks/42\n", encoding="ascii")
    return process


class LinuxHostEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        make_proc(self.root)

    def test_collects_bounded_process_observations_without_attesting(self):
        result = collect_linux_process_snapshot(4242, proc_root=str(self.root))
        self.assertEqual(result.pid, 4242)
        self.assertEqual(result.start_time_ticks, 987654)
        self.assertEqual(dict(result.namespace_links)["net"], "net:[15]")
        self.assertEqual(result.effective_uid, 1000)
        self.assertEqual(result.no_new_privileges, 1)
        self.assertEqual(result.seccomp_mode, 2)
        self.assertEqual(result.effective_capabilities_hex, "0000000000000000")
        self.assertEqual(result.cgroup_v2_path, "/yuki/tasks/42")
        self.assertFalse(result.attested)
        self.assertTrue(result.unknown_controls)

    def test_rejects_invalid_pid_and_root(self):
        with self.assertRaises(SandboxRuntimeError):
            collect_linux_process_snapshot(True, proc_root=str(self.root))
        with self.assertRaises(SandboxRuntimeError):
            collect_linux_process_snapshot(4242, proc_root="relative")

    def test_rejects_symlink_proc_root(self):
        link = self.root.parent / (self.root.name + "-link")
        link.symlink_to(self.root)
        self.addCleanup(link.unlink)
        with self.assertRaises(SandboxRuntimeError):
            collect_linux_process_snapshot(4242, proc_root=str(link))

    def test_rejects_missing_namespace_observation(self):
        (self.root / "4242" / "ns" / "net").unlink()
        with self.assertRaises(SandboxRuntimeError):
            collect_linux_process_snapshot(4242, proc_root=str(self.root))

    def test_rejects_malformed_namespace_identity(self):
        link = self.root / "4242" / "ns" / "net"
        link.unlink()
        link.symlink_to("not-a-namespace")
        with self.assertRaises(SandboxRuntimeError):
            collect_linux_process_snapshot(4242, proc_root=str(self.root))

    def test_rejects_unsafe_cgroup_path(self):
        (self.root / "4242" / "cgroup").write_text("0::/yuki/../host\n", encoding="ascii")
        with self.assertRaises(SandboxRuntimeError):
            collect_linux_process_snapshot(4242, proc_root=str(self.root))

    def test_rejects_noncanonical_cgroup_path(self):
        (self.root / "4242" / "cgroup").write_text("0::/yuki//tasks\n", encoding="ascii")
        with self.assertRaises(SandboxRuntimeError):
            collect_linux_process_snapshot(4242, proc_root=str(self.root))

    def test_rejects_duplicate_security_fields(self):
        status = self.root / "4242" / "status"
        status.write_text(
            "Uid:\t1000\t1000\nUid:\t1000\t1000\n"
            "Gid:\t1001\t1001\nNoNewPrivs:\t1\n"
            "Seccomp:\t2\nCapEff:\t0\n",
            encoding="ascii",
        )
        with self.assertRaises(SandboxRuntimeError):
            collect_linux_process_snapshot(4242, proc_root=str(self.root))

    def test_rejects_missing_security_fields(self):
        status = self.root / "4242" / "status"
        status.write_text("Uid:\t1000\t1000\n", encoding="ascii")
        with self.assertRaises(SandboxRuntimeError):
            collect_linux_process_snapshot(4242, proc_root=str(self.root))


if __name__ == "__main__":
    unittest.main()
