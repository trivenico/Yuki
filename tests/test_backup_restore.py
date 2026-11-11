import io
import os
import stat
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path

from scripts.restore_backup import BackupRestoreError, restore_backup


class SafeBackupRestoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def _tar(self, name, members):
        path = self.root / name
        with tarfile.open(path, "w:gz") as archive:
            for member_name, kind, content in members:
                info = tarfile.TarInfo(member_name)
                if kind == "dir":
                    info.type = tarfile.DIRTYPE
                    info.size = 0
                    archive.addfile(info)
                elif kind == "file":
                    raw = content.encode()
                    info.size = len(raw)
                    archive.addfile(info, io.BytesIO(raw))
                elif kind == "symlink":
                    info.type = tarfile.SYMTYPE
                    info.linkname = content
                    archive.addfile(info)
        return path

    def _zip(self, name, members):
        path = self.root / name
        with zipfile.ZipFile(path, "w") as archive:
            for member_name, content in members:
                archive.writestr(member_name, content)
        return path

    def test_restores_tar_gz_into_new_directory(self):
        archive = self._tar("backup.tar.gz", [
            ("YukiData", "dir", ""),
            ("YukiData/config.yaml", "file", "name: Yuki"),
            ("YukiData/nested/state.json", "file", "{}"),
        ])
        target = restore_backup(archive, self.root / "restored")
        self.assertEqual((target / "YukiData/config.yaml").read_text(), "name: Yuki")
        self.assertEqual((target / "YukiData/nested/state.json").read_text(), "{}")

    def test_restores_windows_zip_backup(self):
        archive = self._zip("backup.zip", [
            ("config.yaml", "name: Yuki"),
            ("nested/state.json", "{}"),
        ])
        target = restore_backup(archive, self.root / "restored")
        self.assertEqual((target / "config.yaml").read_text(), "name: Yuki")

    def test_rejects_parent_traversal(self):
        archive = self._tar("traversal.tar.gz", [("../outside.txt", "file", "bad")])
        with self.assertRaises(BackupRestoreError):
            restore_backup(archive, self.root / "restored")
        self.assertFalse((self.root.parent / "outside.txt").exists())

    def test_rejects_absolute_windows_path(self):
        archive = self._zip("absolute.zip", [("C:/outside.txt", "bad")])
        with self.assertRaises(BackupRestoreError):
            restore_backup(archive, self.root / "restored")

    def test_rejects_symlink_member(self):
        archive = self._tar("symlink.tar.gz", [
            ("link", "symlink", "../../outside"),
        ])
        with self.assertRaises(BackupRestoreError):
            restore_backup(archive, self.root / "restored")

    def test_rejects_duplicate_members(self):
        archive = self._tar("duplicate.tar.gz", [
            ("same.txt", "file", "one"),
            ("same.txt", "file", "two"),
        ])
        with self.assertRaises(BackupRestoreError):
            restore_backup(archive, self.root / "restored")

    def test_refuses_existing_destination(self):
        archive = self._zip("backup.zip", [("state.txt", "ok")])
        destination = self.root / "existing"
        destination.mkdir()
        with self.assertRaises(BackupRestoreError):
            restore_backup(archive, destination)

    def test_failed_restore_removes_temporary_directory(self):
        archive = self._tar("bad.tar.gz", [("../escape", "file", "no")])
        with self.assertRaises(BackupRestoreError):
            restore_backup(archive, self.root / "restored")
        self.assertEqual(list(self.root.glob(".yuki-restore-*")), [])

    @unittest.skipUnless(os.name == "posix", "POSIX permission semantics only")
    def test_restored_files_are_owner_only(self):
        archive = self._zip("backup.zip", [("state.txt", "private")])
        target = restore_backup(archive, self.root / "restored")
        self.assertEqual(stat.S_IMODE((target / "state.txt").stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o700)


if __name__ == "__main__":
    unittest.main()
