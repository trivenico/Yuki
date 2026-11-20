import tempfile
import unittest
from pathlib import Path

from core.linux_lsm_evidence import collect_linux_lsm_snapshot
from core.sandbox_runtime import SandboxRuntimeError


def make_tree(root: Path, *, active="apparmor,selinux", context="yuki-profile (enforce)", enforce="1"):
    proc = root / "proc"
    sys = root / "sys"
    process = proc / "88"
    (process / "attr").mkdir(parents=True)
    (sys / "kernel" / "security").mkdir(parents=True)
    (sys / "fs" / "selinux").mkdir(parents=True)
    suffix = "S " + " ".join(["0"] * 18 + ["777"])
    (process / "stat").write_text(f"88 (yuki worker) {suffix}\n", encoding="ascii")
    (process / "attr" / "current").write_text(context + "\n", encoding="ascii")
    (sys / "kernel" / "security" / "lsm").write_text(active + "\n", encoding="ascii")
    (sys / "fs" / "selinux" / "enforce").write_text(enforce + "\n", encoding="ascii")
    return proc, sys, process


class LinuxLsmEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.proc, self.sys, self.process = make_tree(self.root)

    def collect(self):
        return collect_linux_lsm_snapshot(88, proc_root=str(self.proc), sys_root=str(self.sys))

    def test_collects_apparmor_and_selinux_observations_without_attesting(self):
        report = self.collect()
        self.assertEqual(report.active_lsms, ("apparmor", "selinux"))
        self.assertEqual(report.apparmor_profile, "yuki-profile")
        self.assertEqual(report.apparmor_mode, "enforce")
        self.assertEqual(report.selinux_mode, "enforcing")
        self.assertFalse(report.attested)
        self.assertFalse(report.verified)
        self.assertTrue(report.unknown_controls)

    def test_reports_permissive_selinux_and_complain_apparmor(self):
        (self.process / "attr" / "current").write_text("profile-x (complain)", encoding="ascii")
        (self.sys / "fs" / "selinux" / "enforce").write_text("0", encoding="ascii")
        report = self.collect()
        self.assertEqual(report.apparmor_mode, "complain")
        self.assertEqual(report.selinux_mode, "permissive")

    def test_missing_optional_kernel_files_are_reported(self):
        (self.sys / "kernel" / "security" / "lsm").unlink()
        (self.sys / "fs" / "selinux" / "enforce").unlink()
        report = self.collect()
        self.assertIsNone(report.active_lsms)
        self.assertIsNone(report.selinux_mode)
        self.assertIn("sysfs:kernel/security/lsm", report.unavailable_sources)

    def test_rejects_malformed_lsm_list(self):
        (self.sys / "kernel" / "security" / "lsm").write_text("apparmor,,selinux", encoding="ascii")
        with self.assertRaises(SandboxRuntimeError):
            self.collect()

    def test_rejects_duplicate_lsm_names(self):
        (self.sys / "kernel" / "security" / "lsm").write_text("apparmor,apparmor", encoding="ascii")
        with self.assertRaises(SandboxRuntimeError):
            self.collect()

    def test_rejects_malformed_selinux_mode(self):
        (self.sys / "fs" / "selinux" / "enforce").write_text("enabled", encoding="ascii")
        with self.assertRaises(SandboxRuntimeError):
            self.collect()

    def test_rejects_symlinked_security_context(self):
        target = self.root / "context"
        target.write_text("profile (enforce)", encoding="ascii")
        context = self.process / "attr" / "current"
        context.unlink()
        context.symlink_to(target)
        with self.assertRaises(SandboxRuntimeError):
            self.collect()

    def test_rejects_symlinked_roots(self):
        link = self.root / "proc-link"
        link.symlink_to(self.proc)
        with self.assertRaises(SandboxRuntimeError):
            collect_linux_lsm_snapshot(88, proc_root=str(link), sys_root=str(self.sys))

    def test_rejects_invalid_context_control_characters(self):
        (self.process / "attr" / "current").write_text("profile\x01", encoding="ascii")
        with self.assertRaises(SandboxRuntimeError):
            self.collect()


if __name__ == "__main__":
    unittest.main()
