import os
import tempfile
import unittest
from pathlib import Path

from core.linux_mount_evidence import collect_linux_mount_snapshot, parse_mountinfo
from core.sandbox_runtime import SandboxRuntimeError


@unittest.skipUnless(os.name == "posix", "Linux diagnostic fixture requires POSIX filesystem semantics")
class LinuxMountEvidenceTests(unittest.TestCase):
    def test_parses_mount_and_superblock_read_only_flags(self):
        payload = (
            "36 25 0:32 / /workspace ro,nosuid,nodev - ext4 /dev/loop0 ro,errors=remount-ro\n"
            "37 36 0:33 / /workspace/tmp rw,nosuid - tmpfs tmpfs rw,size=64k\n"
        )
        result = parse_mountinfo(payload, pid=123)
        self.assertEqual(len(result.entries), 2)
        self.assertTrue(result.entries[0].mount_read_only)
        self.assertTrue(result.entries[0].superblock_read_only)
        self.assertFalse(result.entries[1].mount_read_only)
        self.assertFalse(result.entries[1].superblock_read_only)
        self.assertFalse(result.attested)
        self.assertTrue(result.unknown_controls)

    def test_decodes_procfs_escaped_mount_paths_and_backslash(self):
        payload = "40 25 0:40 /root\\040dir /mnt/a\\134b rw - ext4 /dev/test rw\n"
        result = parse_mountinfo(payload, pid=4)
        self.assertEqual(result.entries[0].root, "/root dir")
        self.assertEqual(result.entries[0].mount_point, "/mnt/a\\b")

    def test_rejects_malformed_escape(self):
        with self.assertRaises(SandboxRuntimeError):
            parse_mountinfo("1 0 0:1 / /bad\\xyz rw - tmpfs tmpfs rw\n", pid=1)

    def test_rejects_duplicate_mount_ids(self):
        line = "1 0 0:1 / / rw - tmpfs tmpfs rw\n"
        with self.assertRaises(SandboxRuntimeError):
            parse_mountinfo(line + line, pid=1)

    def test_rejects_malformed_field_count_and_device(self):
        for payload in (
            "1 0 0:1 / / rw - tmpfs tmpfs\n",
            "1 0 x:y / / rw - tmpfs tmpfs rw\n",
        ):
            with self.subTest(payload=payload), self.assertRaises(SandboxRuntimeError):
                parse_mountinfo(payload, pid=1)

    def test_rejects_oversized_mount_table(self):
        with self.assertRaises(SandboxRuntimeError):
            parse_mountinfo("x" * (1_048_577), pid=1)

    def test_reads_mountinfo_without_following_symlinks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            proc = root / "proc"
            (proc / "123").mkdir(parents=True)
            target = root / "target"
            target.write_text("36 25 0:32 / / rw - tmpfs tmpfs rw\n", encoding="ascii")
            (proc / "123" / "mountinfo").symlink_to(target)
            with self.assertRaises(SandboxRuntimeError):
                collect_linux_mount_snapshot(123, proc_root=str(proc))

    def test_collects_regular_mountinfo_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc = Path(tmp)
            process = proc / "123"
            process.mkdir()
            (process / "mountinfo").write_text(
                "36 25 0:32 / / rw - tmpfs tmpfs rw\n", encoding="ascii"
            )
            result = collect_linux_mount_snapshot(123, proc_root=str(proc))
            self.assertEqual(result.pid, 123)
            self.assertEqual(result.entries[0].filesystem_type, "tmpfs")
            self.assertFalse(result.attested)


if __name__ == "__main__":
    unittest.main()
