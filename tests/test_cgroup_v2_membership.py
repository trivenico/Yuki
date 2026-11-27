import os
import tempfile
import unittest
from pathlib import Path

from evaluation.cgroup_v2_membership import (
    parse_cgroup2_mountinfo,
    verify_process_cgroup_membership,
    _process_start_time,
    _unified_cgroup_path,
)


class CgroupV2MembershipTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.mount = self.base / "cgroup mount"
        self.child = self.mount / "delegated" / "runner"
        self.child.mkdir(parents=True)
        self.proc = self.base / "proc"
        self.pid = 4242
        self.process = self.proc / str(self.pid)
        self.process.mkdir(parents=True)
        (self.child / "cgroup.procs").write_text(f"{self.pid}\n")
        (self.process / "cgroup").write_text("0::/delegated/runner\n")
        (self.process / "stat").write_text(self._stat(987654))
        mountpoint = str(self.mount).replace(" ", "\\040")
        (self.process / "mountinfo").write_text(
            f"36 25 0:32 / {mountpoint} rw,nosuid - cgroup2 cgroup rw\n"
        )

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def _stat(start_time):
        fields = ["0"] * 20
        fields[0] = "S"
        fields[19] = str(start_time)
        return "4242 (worker process) " + " ".join(fields) + "\n"

    def test_verifies_mount_mapping_pid_identity_and_membership(self):
        result = verify_process_cgroup_membership(
            self.pid, expected_cgroup_path=str(self.child), proc_root=self.proc
        )
        self.assertTrue(result.verified)
        self.assertEqual(result.mount_id, 36)
        self.assertEqual(result.process_start_time, 987654)
        self.assertEqual(result.reason, "membership_verified_snapshot")

    def test_pinned_verifier_binds_root_descriptor_to_mountinfo_id(self):
        from unittest.mock import patch
        from evaluation.cgroup_v2_membership import verify_process_cgroup_membership_pinned
        root_fd = os.open(self.mount, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        try:
            with patch("evaluation.cgroup_v2_fd._mount_id", return_value=36):
                with patch("evaluation.cgroup_v2_membership._descriptor_mount_id", return_value=36):
                    result = verify_process_cgroup_membership_pinned(
                        self.pid,
                        trusted_mount_root_fd=root_fd,
                        expected_relative_path="delegated/runner",
                        proc_root=self.proc,
                    )
            self.assertTrue(result.verified)
            self.assertEqual(result.mount_id, 36)
            self.assertEqual(result.reason, "pinned_membership_verified_snapshot")
        finally:
            os.close(root_fd)

    def test_pinned_verifier_rejects_unmatched_mount_id(self):
        from unittest.mock import patch
        from evaluation.cgroup_v2_membership import verify_process_cgroup_membership_pinned
        root_fd = os.open(self.mount, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        try:
            with patch("evaluation.cgroup_v2_fd._mount_id", return_value=36):
                with patch("evaluation.cgroup_v2_membership._descriptor_mount_id", return_value=99):
                    result = verify_process_cgroup_membership_pinned(
                        self.pid,
                        trusted_mount_root_fd=root_fd,
                        expected_relative_path="delegated/runner",
                        proc_root=self.proc,
                    )
            self.assertFalse(result.verified)
        finally:
            os.close(root_fd)

    def test_bounded_reader_rejects_fifo_without_blocking(self):
        from evaluation.cgroup_v2_membership import _read_bounded
        fifo = self.base / "proc-fifo"
        os.mkfifo(fifo)
        with self.assertRaises(ValueError):
            _read_bounded(fifo)

    def test_bounded_reader_rejects_symlink(self):
        from evaluation.cgroup_v2_membership import _read_bounded
        target = self.base / "regular-proc-file"
        target.write_text("safe\\n")
        link = self.base / "proc-link"
        link.symlink_to(target)
        with self.assertRaises(OSError):
            _read_bounded(link)

    def test_descriptor_relative_reader_rejects_symlink_and_nested_names(self):
        from evaluation.cgroup_v2_membership import _read_bounded_at
        directory = self.base / "pinned-proc"
        directory.mkdir()
        target = directory / "target"
        target.write_text("safe\\n")
        (directory / "link").symlink_to(target)
        fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        try:
            with self.assertRaises(OSError):
                _read_bounded_at(fd, "link")
            with self.assertRaises(ValueError):
                _read_bounded_at(fd, "../target")
            self.assertEqual(_read_bounded_at(fd, "target"), "safe\\n")
        finally:
            os.close(fd)

    def test_rejects_pid_not_listed_in_cgroup_procs(self):
        (self.child / "cgroup.procs").write_text("9999\n")
        result = verify_process_cgroup_membership(
            self.pid, expected_cgroup_path=str(self.child), proc_root=self.proc
        )
        self.assertFalse(result.verified)

    def test_rejects_mismatched_expected_cgroup_directory(self):
        other = self.mount / "other"
        other.mkdir()
        (other / "cgroup.procs").write_text(f"{self.pid}\n")
        result = verify_process_cgroup_membership(
            self.pid, expected_cgroup_path=str(other), proc_root=self.proc
        )
        self.assertFalse(result.verified)

    def test_accepts_root_unified_cgroup_path(self):
        self.assertEqual(_unified_cgroup_path("0::/\\n"), "/")

    def test_rejects_traversal_in_kernel_reported_cgroup_path(self):
        for path in ("/delegated/../runner", "/delegated//runner", "/delegated/./runner"):
            with self.subTest(path=path):
                (self.process / "cgroup").write_text(f"0::{path}\n")
                result = verify_process_cgroup_membership(
                    self.pid, expected_cgroup_path=str(self.child), proc_root=self.proc
                )
                self.assertFalse(result.verified)

    def test_rejects_multiple_or_missing_unified_mount_matches(self):
        (self.process / "cgroup").write_text("0::/not-mounted\n")
        result = verify_process_cgroup_membership(
            self.pid, expected_cgroup_path=str(self.child), proc_root=self.proc
        )
        self.assertFalse(result.verified)

    def test_rejects_noncanonical_cgroup_path(self):
        result = verify_process_cgroup_membership(
            self.pid, expected_cgroup_path=str(self.mount / ".." / "elsewhere"),
            proc_root=self.proc,
        )
        self.assertFalse(result.verified)

    def test_rejects_empty_components_in_expected_cgroup_path(self):
        for path in (
            str(self.mount) + "//delegated/runner",
            str(self.mount) + "/delegated//runner",
            str(self.mount) + "/delegated/runner/",
        ):
            with self.subTest(path=path):
                result = verify_process_cgroup_membership(
                    self.pid, expected_cgroup_path=path, proc_root=self.proc
                )
                self.assertFalse(result.verified)

    def test_rejects_non_ascii_or_zero_process_start_time(self):
        for start_time in ("0", "٩٨٧٦٥٤"):
            with self.subTest(start_time=start_time):
                (self.process / "stat").write_text(self._stat(start_time))
                with self.assertRaises(ValueError):
                    _process_start_time((self.process / "stat").read_text())

    def test_rejects_invalid_pid_types_and_ranges(self):
        for pid in (True, 0, -1, "4242"):
            result = verify_process_cgroup_membership(
                pid, expected_cgroup_path=str(self.child), proc_root=self.proc
            )
            self.assertFalse(result.verified)

    def test_mountinfo_parser_decodes_escaped_mountpoint(self):
        parsed = parse_cgroup2_mountinfo(
            "36 25 0:32 / /tmp/cgroup\\040mount rw - cgroup2 cgroup rw\n"
        )
        self.assertEqual(parsed, ((36, "/", "/tmp/cgroup mount", "0:32"),))

    def test_mountinfo_parser_rejects_unsupported_octal_escapes(self):
        for escape in ("\\057", "\\777"):
            with self.subTest(escape=escape):
                with self.assertRaises(ValueError):
                    parse_cgroup2_mountinfo(
                        f"36 25 0:32 / /tmp/cgroup{escape}mount rw - cgroup2 cgroup rw\\n"
                    )

    def test_mountinfo_parser_accepts_only_documented_escapes(self):
        parsed = parse_cgroup2_mountinfo(
            "36 25 0:32 / /tmp/a\\040b\\011c\\012d\\134e rw - cgroup2 cgroup rw\\n"
        )
        self.assertEqual(parsed[0][2], "/tmp/a b\\tc\\nd\\\\e")

    def test_mountinfo_parser_rejects_dangling_backslash(self):
        with self.assertRaises(ValueError):
            parse_cgroup2_mountinfo(
                "36 25 0:32 / /tmp/cgroup\\mount rw - cgroup2 cgroup rw\\n"
            )

    def test_mountinfo_parser_rejects_nonpositive_or_non_ascii_mount_ids(self):
        for mount_id, parent_id in (("0", "25"), ("36", "0"), ("٣٦", "25")):
            with self.subTest(mount_id=mount_id, parent_id=parent_id):
                with self.assertRaises(ValueError):
                    parse_cgroup2_mountinfo(
                        f"{mount_id} {parent_id} 0:32 / /tmp/cgroup rw - cgroup2 cgroup rw\\n"
                    )

    def test_mountinfo_parser_rejects_noncanonical_mount_paths(self):
        for root, mountpoint in (
            ("/delegated/../runner", "/tmp/cgroup"),
            ("/delegated//runner", "/tmp/cgroup"),
            ("/", "/tmp/./cgroup"),
            ("/", "/tmp//cgroup"),
        ):
            with self.subTest(root=root, mountpoint=mountpoint):
                with self.assertRaises(ValueError):
                    parse_cgroup2_mountinfo(
                        f"36 25 0:32 {root} {mountpoint} rw - cgroup2 cgroup rw\\n"
                    )

    def test_mountinfo_parser_rejects_malformed_records(self):
        with self.assertRaises(ValueError):
            parse_cgroup2_mountinfo("not a mountinfo record")


if __name__ == "__main__":
    unittest.main()
