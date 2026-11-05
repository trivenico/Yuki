import os
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path

from providers.dataset_archive_inspector import (
    DatasetArchiveError,
    inspect_dataset_archive,
)


class DatasetArchiveInspectorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def _zip(self, name, members):
        path = self.root / name
        with zipfile.ZipFile(path, "w") as archive:
            for member, data in members:
                archive.writestr(member, data)
        return path

    def test_inspects_safe_zip_without_extracting(self):
        path = self._zip("safe.zip", [("records/data.jsonl", '{"ok":true}\n')])
        result = inspect_dataset_archive(path)
        self.assertEqual(result["format"], "zip")
        self.assertEqual(result["member_count"], 1)
        self.assertEqual(result["entries"][0]["path"], "records/data.jsonl")
        self.assertFalse((self.root / "records").exists())

    def test_rejects_traversal_and_absolute_paths(self):
        for name in ("../escape.txt", "/absolute.txt", "C:\\escape.txt"):
            with self.subTest(name=name):
                path = self._zip("unsafe.zip", [(name, "bad")])
                with self.assertRaises(DatasetArchiveError):
                    inspect_dataset_archive(path)

    def test_rejects_duplicate_members(self):
        path = self._zip("duplicate.zip", [("data.jsonl", "a"), ("data.jsonl", "b")])
        with self.assertRaisesRegex(DatasetArchiveError, "duplicate_archive_member"):
            inspect_dataset_archive(path)

    def test_rejects_zip_symlink(self):
        path = self.root / "link.zip"
        info = zipfile.ZipInfo("link")
        info.create_system = 3
        info.external_attr = (0o120777 << 16)
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr(info, "target")
        with self.assertRaisesRegex(DatasetArchiveError, "unsupported_archive_member_type"):
            inspect_dataset_archive(path)

    def test_rejects_tar_symlink(self):
        path = self.root / "link.tar"
        with tarfile.open(path, "w") as archive:
            info = tarfile.TarInfo("link")
            info.type = tarfile.SYMTYPE
            info.linkname = "target"
            archive.addfile(info)
        with self.assertRaisesRegex(DatasetArchiveError, "unsupported_archive_member_type"):
            inspect_dataset_archive(path)

    def test_enforces_member_and_total_size_limits(self):
        path = self._zip("large.zip", [("payload.bin", "12345")])
        with self.assertRaisesRegex(DatasetArchiveError, "archive_member_size_limit_exceeded"):
            inspect_dataset_archive(path, max_member_bytes=4)
        with self.assertRaisesRegex(DatasetArchiveError, "archive_total_size_limit_exceeded"):
            inspect_dataset_archive(path, max_total_bytes=4)

    def test_rejects_invalid_limits_and_symlink_archive(self):
        path = self._zip("valid.zip", [("data.txt", "ok")])
        with self.assertRaisesRegex(DatasetArchiveError, "invalid_max_members"):
            inspect_dataset_archive(path, max_members=True)
        link = self.root / "archive-link.zip"
        try:
            link.symlink_to(path)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks unavailable")
        with self.assertRaisesRegex(DatasetArchiveError, "archive_must_be_regular"):
            inspect_dataset_archive(link)


if __name__ == "__main__":
    unittest.main()
