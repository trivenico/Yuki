import os
import stat
import tempfile
import unittest
import zipfile
from pathlib import Path

from scripts.portable_archive import ArchiveValidationError, extract_zip_safely


class PortableArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.archive = self.root / "runtime.zip"
        self.destination = self.root / "runtime"

    def _zip(self, entries):
        with zipfile.ZipFile(self.archive, "w") as bundle:
            for name, data, mode in entries:
                info = zipfile.ZipInfo(name)
                if mode is not None:
                    info.external_attr = mode << 16
                bundle.writestr(info, data)

    def test_extracts_regular_files_and_preserves_executable_bit(self):
        self._zip([("python/bin/python", b"runtime", stat.S_IFREG | 0o755), ("python/README.txt", b"docs", stat.S_IFREG | 0o644)])
        report = extract_zip_safely(self.archive, self.destination)
        self.assertEqual(report["files"], 2)
        self.assertEqual((self.destination / "python/README.txt").read_bytes(), b"docs")
        if os.name != "nt":
            self.assertTrue((self.destination / "python/bin/python").stat().st_mode & stat.S_IXUSR)

    def test_rejects_parent_traversal(self):
        self._zip([("../outside.txt", b"bad", stat.S_IFREG | 0o644)])
        with self.assertRaisesRegex(ArchiveValidationError, "traverses"):
            extract_zip_safely(self.archive, self.destination)
        self.assertFalse((self.root / "outside.txt").exists())
        self.assertFalse(self.destination.exists())

    def test_rejects_absolute_and_backslash_paths(self):
        for name in ("/absolute.txt", "folder\\..\\escape.txt"):
            with self.subTest(name=name):
                self._zip([(name, b"bad", stat.S_IFREG | 0o644)])
                with self.assertRaises(ArchiveValidationError):
                    extract_zip_safely(self.archive, self.destination)

    def test_rejects_symlink_members(self):
        self._zip([("link", "../outside", stat.S_IFLNK | 0o777)])
        with self.assertRaisesRegex(ArchiveValidationError, "Symbolic links"):
            extract_zip_safely(self.archive, self.destination)

    def test_rejects_special_files(self):
        self._zip([("device", b"", stat.S_IFCHR | 0o600)])
        with self.assertRaisesRegex(ArchiveValidationError, "Special files"):
            extract_zip_safely(self.archive, self.destination)

    def test_rejects_case_collisions(self):
        self._zip([("Python/bin", b"one", stat.S_IFREG | 0o644), ("python/BIN", b"two", stat.S_IFREG | 0o644)])
        with self.assertRaisesRegex(ArchiveValidationError, "case-colliding"):
            extract_zip_safely(self.archive, self.destination)

    def test_rejects_file_parent_collision(self):
        self._zip([("python", b"file", stat.S_IFREG | 0o644), ("python/bin", b"child", stat.S_IFREG | 0o644)])
        with self.assertRaisesRegex(ArchiveValidationError, "parent directory"):
            extract_zip_safely(self.archive, self.destination)

    def test_rejects_expanded_size_limit(self):
        self._zip([("large.bin", b"x" * 128, stat.S_IFREG | 0o644)])
        with self.assertRaisesRegex(ArchiveValidationError, "per-file size"):
            extract_zip_safely(self.archive, self.destination, max_file_bytes=64)

    def test_rejects_member_count_limit(self):
        self._zip([("one", b"1", stat.S_IFREG | 0o644), ("two", b"2", stat.S_IFREG | 0o644)])
        with self.assertRaisesRegex(ArchiveValidationError, "member-count"):
            extract_zip_safely(self.archive, self.destination, max_files=1)

    def test_destination_must_not_exist(self):
        self._zip([("runtime.bin", b"ok", stat.S_IFREG | 0o644)])
        self.destination.mkdir()
        with self.assertRaisesRegex(ArchiveValidationError, "must not already exist"):
            extract_zip_safely(self.archive, self.destination)

    def test_rejects_symlink_archive(self):
        self._zip([("runtime.bin", b"ok", stat.S_IFREG | 0o644)])
        alias = self.root / "alias.zip"
        try:
            alias.symlink_to(self.archive)
        except (OSError, NotImplementedError):
            self.skipTest("Symlinks unavailable on this runner")
        with self.assertRaisesRegex(ArchiveValidationError, "non-symlink"):
            extract_zip_safely(alias, self.destination)


if __name__ == "__main__":
    unittest.main()
