import os
from pathlib import Path
import stat
import tarfile
import tempfile
import unittest
import zipfile

from scripts.restore_workspace import RestoreError, restore_workspace


class WorkspaceRestoreTests(unittest.TestCase):
    def test_restores_unix_tar_workspace_and_verifies_sqlite(self):
        import sqlite3

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "workspace"
            source.mkdir()
            db = sqlite3.connect(source / "memory.sqlite3")
            try:
                db.execute("CREATE TABLE memories (content TEXT)")
                db.execute("INSERT INTO memories VALUES ('safe restore')")
                db.commit()
            finally:
                db.close()
            (source / "private.json").write_text('{"ok":true}', encoding="utf-8")
            archive_path = root / "backup.tar.gz"
            with tarfile.open(archive_path, "w:gz") as archive:
                archive.add(source, arcname="workspace")
            destination = root / "restored"
            result = restore_workspace(archive_path, destination)
            self.assertEqual(result["files_restored"], 2)
            self.assertEqual(result["sqlite_databases_verified"], 1)
            self.assertEqual((destination / "private.json").read_text(encoding="utf-8"), '{"ok":true}')
            restored = sqlite3.connect(destination / "memory.sqlite3")
            try:
                self.assertEqual(restored.execute("SELECT content FROM memories").fetchone()[0], "safe restore")
            finally:
                restored.close()

    def test_restores_windows_zip_with_root_files(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            archive_path = root / "backup.zip"
            with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                archive.writestr("visible.json", '{"ok":true}')
                archive.writestr(".private-state", "hidden")
            destination = root / "restored"
            result = restore_workspace(archive_path, destination)
            self.assertEqual(result["files_restored"], 2)
            self.assertEqual((destination / "visible.json").read_text(encoding="utf-8"), '{"ok":true}')
            self.assertEqual((destination / ".private-state").read_text(encoding="utf-8"), "hidden")
            if os.name != "nt":
                self.assertEqual(stat.S_IMODE(destination.stat().st_mode), 0o700)
                self.assertEqual(stat.S_IMODE((destination / "visible.json").stat().st_mode), 0o600)

    def test_rejects_existing_destination_without_overwriting(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            archive_path = root / "backup.zip"
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("note.txt", "backup")
            destination = root / "existing"
            destination.mkdir()
            (destination / "keep.txt").write_text("keep", encoding="utf-8")
            with self.assertRaises(RestoreError):
                restore_workspace(archive_path, destination)
            self.assertEqual((destination / "keep.txt").read_text(encoding="utf-8"), "keep")

    def test_rejects_zip_path_traversal_and_cleans_staging(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            archive_path = root / "malicious.zip"
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("../escape.txt", "outside")
            destination = root / "restored"
            with self.assertRaises(RestoreError):
                restore_workspace(archive_path, destination)
            self.assertFalse((root / "escape.txt").exists())
            self.assertFalse(destination.exists())
            self.assertEqual(list(root.glob(".yuki-restore-*")), [])

    def test_rejects_tar_symlinks_and_hardlinks(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            archive_path = root / "malicious.tar.gz"
            with tarfile.open(archive_path, "w:gz") as archive:
                link = tarfile.TarInfo("workspace/outside")
                link.type = tarfile.SYMTYPE
                link.linkname = "/etc/passwd"
                archive.addfile(link)
            with self.assertRaises(RestoreError):
                restore_workspace(archive_path, root / "restored")
            self.assertFalse((root / "restored").exists())

    def test_rejects_duplicate_paths_case_insensitively(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            archive_path = root / "duplicate.zip"
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("Notes.txt", "one")
                archive.writestr("notes.TXT", "two")
            with self.assertRaises(RestoreError):
                restore_workspace(archive_path, root / "restored")

    def test_rejects_corrupt_sqlite_before_publishing_restore(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            archive_path = root / "bad-db.zip"
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("broken.sqlite3", b"SQLite format 3\x00" + b"corrupt")
            destination = root / "restored"
            with self.assertRaises(RestoreError):
                restore_workspace(archive_path, destination)
            self.assertFalse(destination.exists())
            self.assertEqual(list(root.glob(".yuki-restore-*")), [])

    def test_rejects_absolute_and_windows_drive_paths(self):
        for member_name in ("/absolute.txt", "C:/windows.txt", "folder\\..\\escape.txt"):
            with self.subTest(member_name=member_name), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                archive_path = root / "malicious.zip"
                with zipfile.ZipFile(archive_path, "w") as archive:
                    archive.writestr(member_name, "bad")
                with self.assertRaises(RestoreError):
                    restore_workspace(archive_path, root / "restored")

    def test_rejects_truncated_archive(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            archive_path = root / "truncated.zip"
            archive_path.write_bytes(b"PK\x03\x04broken")
            with self.assertRaises(RestoreError):
                restore_workspace(archive_path, root / "restored")

    def test_rejects_sqlite_journal_sidecar(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            archive_path = root / "sidecar.zip"
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("memory.sqlite3-wal", "untrusted journal")
            with self.assertRaises(RestoreError):
                restore_workspace(archive_path, root / "restored")

    def test_rejects_destination_symlink(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            archive_path = root / "backup.zip"
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("note.txt", "safe")
            real_destination = root / "real"
            real_destination.mkdir()
            link = root / "linked"
            try:
                link.symlink_to(real_destination, target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest("Symlink creation is not available on this platform")
            with self.assertRaises(RestoreError):
                restore_workspace(archive_path, link)
            self.assertEqual(list(real_destination.iterdir()), [])

    def test_rejects_case_colliding_directory_paths(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            archive_path = root / "case-collision.zip"
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("Folder/one.txt", "one")
                archive.writestr("folder/two.txt", "two")
            with self.assertRaises(RestoreError):
                restore_workspace(archive_path, root / "restored")


if __name__ == "__main__":
    unittest.main()
