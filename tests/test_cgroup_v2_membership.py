import tempfile
import unittest
from pathlib import Path

from evaluation.cgroup_v2_membership import (
    parse_cgroup2_mountinfo,
    verify_process_cgroup_membership,
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

    def test_mountinfo_parser_rejects_malformed_records(self):
        with self.assertRaises(ValueError):
            parse_cgroup2_mountinfo("not a mountinfo record")


if __name__ == "__main__":
    unittest.main()
