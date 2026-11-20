import tempfile
import unittest
from pathlib import Path

from core.linux_sandbox_evidence import collect_linux_sandbox_evidence
from core.sandbox_runtime import SandboxRuntimeError


def make_fixture(root: Path):
    proc = root / "proc"
    cgroup = root / "cgroup"
    process = proc / "123"
    ns = process / "ns"
    ns.mkdir(parents=True)
    (process / "stat").write_text("123 (worker ) name) S " + " ".join(["0"] * 18) + " 987654\n", encoding="ascii")
    (process / "status").write_text(
        "Uid:\t1000\t1000\t1000\t1000\n"
        "Gid:\t1000\t1000\t1000\t1000\n"
        "NoNewPrivs:\t1\nSeccomp:\t2\nCapEff:\t0000000000000000\n",
        encoding="ascii",
    )
    for name in ("user", "pid", "mnt", "ipc", "net"):
        (ns / name).symlink_to("user:[4026531837]")
    (process / "cgroup").write_text("0::/yuki/job-123\n", encoding="ascii")
    (cgroup / "cgroup.controllers").write_text("cpu memory pids\n", encoding="ascii")
    leaf = cgroup / "yuki" / "job-123"
    leaf.mkdir(parents=True)
    (leaf / "cpu.max").write_text("20000 100000\n", encoding="ascii")
    (leaf / "memory.max").write_text("268435456\n", encoding="ascii")
    (leaf / "memory.swap.max").write_text("0\n", encoding="ascii")
    (leaf / "pids.max").write_text("32\n", encoding="ascii")
    return proc, cgroup


class LinuxSandboxEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.proc, self.cgroup = make_fixture(self.root)

    def collect(self):
        return collect_linux_sandbox_evidence(
            123, proc_root=str(self.proc), cgroup_root=str(self.cgroup)
        )

    def test_combines_process_and_cgroup_with_versioned_non_attesting_envelope(self):
        report = self.collect()
        self.assertEqual(report.schema_version, 1)
        self.assertEqual(report.evidence_class, "diagnostic_only")
        self.assertEqual(report.process.pid, 123)
        self.assertEqual(report.cgroup.cgroup_path, "/yuki/job-123")
        self.assertEqual(report.cgroup.memory_max_bytes, 268435456)
        self.assertFalse(report.attested)
        self.assertFalse(report.verified)
        self.assertGreaterEqual(report.collection_duration_ns, 0)
        self.assertLessEqual(report.observation_started_unix_ns, report.observation_finished_unix_ns)
        self.assertTrue(any("not an atomic snapshot" in item for item in report.unknown_controls))

    def test_rejects_process_without_cgroup_v2_membership(self):
        (self.proc / "123" / "cgroup").write_text("5:cpu:/legacy\n", encoding="ascii")
        with self.assertRaises(SandboxRuntimeError):
            self.collect()

    def test_rejects_missing_required_cgroup_controls(self):
        (self.cgroup / "yuki" / "job-123" / "pids.max").unlink()
        with self.assertRaises(SandboxRuntimeError):
            self.collect()

    def test_rejects_missing_process_identity(self):
        with self.assertRaises(SandboxRuntimeError):
            collect_linux_sandbox_evidence(
                999, proc_root=str(self.proc), cgroup_root=str(self.cgroup)
            )


if __name__ == "__main__":
    unittest.main()
