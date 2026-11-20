import tempfile
import unittest
from pathlib import Path

from core.linux_userns_evidence import collect_linux_user_namespace_snapshot
from core.sandbox_runtime import SandboxRuntimeError


def make_proc(root: Path, pid: int = 77):
    process = root / str(pid)
    process.mkdir()
    suffix = "S " + " ".join(["0"] * 18 + ["123456"])
    (process / "stat").write_text(f"{pid} (userns worker) {suffix}\n", encoding="ascii")
    (process / "uid_map").write_text("0 100000 65536\n", encoding="ascii")
    (process / "gid_map").write_text("0 200000 65536\n", encoding="ascii")
    (process / "setgroups").write_text("deny\n", encoding="ascii")
    return process


class LinuxUserNamespaceEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.process = make_proc(self.root)

    def test_collects_maps_without_attesting(self):
        report = collect_linux_user_namespace_snapshot(77, proc_root=str(self.root))
        self.assertEqual(report.uid_map[0].inside_start, 0)
        self.assertEqual(report.uid_map[0].outside_start, 100000)
        self.assertEqual(report.uid_map[0].length, 65536)
        self.assertEqual(report.gid_map[0].outside_start, 200000)
        self.assertEqual(report.setgroups, "deny")
        self.assertFalse(report.attested)
        self.assertFalse(report.verified)

    def test_accepts_missing_setgroups_file(self):
        (self.process / "setgroups").unlink()
        report = collect_linux_user_namespace_snapshot(77, proc_root=str(self.root))
        self.assertIsNone(report.setgroups)

    def test_rejects_malformed_or_empty_maps(self):
        (self.process / "uid_map").write_text("0 1000\n", encoding="ascii")
        with self.assertRaises(SandboxRuntimeError):
            collect_linux_user_namespace_snapshot(77, proc_root=str(self.root))
        (self.process / "uid_map").write_text("", encoding="ascii")
        with self.assertRaises(SandboxRuntimeError):
            collect_linux_user_namespace_snapshot(77, proc_root=str(self.root))

    def test_rejects_overlapping_inside_ranges(self):
        (self.process / "uid_map").write_text("0 1000 10\n5 2000 10\n", encoding="ascii")
        with self.assertRaises(SandboxRuntimeError):
            collect_linux_user_namespace_snapshot(77, proc_root=str(self.root))

    def test_rejects_overlapping_outside_ranges(self):
        (self.process / "gid_map").write_text("0 1000 10\n20 1005 10\n", encoding="ascii")
        with self.assertRaises(SandboxRuntimeError):
            collect_linux_user_namespace_snapshot(77, proc_root=str(self.root))

    def test_rejects_integer_overflow_and_zero_length(self):
        (self.process / "uid_map").write_text("0 4294967295 2\n", encoding="ascii")
        with self.assertRaises(SandboxRuntimeError):
            collect_linux_user_namespace_snapshot(77, proc_root=str(self.root))
        (self.process / "uid_map").write_text("0 1000 0\n", encoding="ascii")
        with self.assertRaises(SandboxRuntimeError):
            collect_linux_user_namespace_snapshot(77, proc_root=str(self.root))

    def test_rejects_invalid_setgroups(self):
        (self.process / "setgroups").write_text("sometimes\n", encoding="ascii")
        with self.assertRaises(SandboxRuntimeError):
            collect_linux_user_namespace_snapshot(77, proc_root=str(self.root))

    def test_rejects_symlinked_map(self):
        target = self.root / "uid-target"
        target.write_text("0 100000 65536\n", encoding="ascii")
        (self.process / "uid_map").unlink()
        (self.process / "uid_map").symlink_to(target)
        with self.assertRaises(SandboxRuntimeError):
            collect_linux_user_namespace_snapshot(77, proc_root=str(self.root))


if __name__ == "__main__":
    unittest.main()
