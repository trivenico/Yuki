import importlib.util
import sqlite3
import tempfile
import unittest
from pathlib import Path

from ui.desktop.backup import BackupError, create_encrypted_backup, restore_encrypted_backup


@unittest.skipUnless(importlib.util.find_spec("cryptography"), "cryptography optional extra is not installed")
class DesktopBackupTests(unittest.TestCase):
    def test_encrypted_backup_restores_files_and_preserves_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "data"
            source.mkdir()
            (source / "memory.json").write_text('{"private": true}', encoding="utf-8")
            backup = root / "yuki.yukibak"
            result = create_encrypted_backup(source, backup, "correct horse battery staple")
            self.assertEqual(result["file_count"], 1)
            self.assertNotIn(b"private", backup.read_bytes())
            destination = root / "restored"
            restored = restore_encrypted_backup(backup, destination, "correct horse battery staple")
            self.assertEqual(restored["file_count"], 1)
            self.assertEqual((destination / "memory.json").read_text(), '{"private": true}')
            self.assertTrue((source / "memory.json").exists())

    def test_wrong_passphrase_does_not_create_destination(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "data"
            source.mkdir()
            (source / "x.txt").write_text("secret")
            backup = root / "backup"
            create_encrypted_backup(source, backup, "a sufficiently long passphrase")
            destination = root / "restore"
            with self.assertRaises(BackupError):
                restore_encrypted_backup(backup, destination, "a different passphrase")
            self.assertFalse(destination.exists())

    def test_nonempty_destination_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "data"
            source.mkdir()
            (source / "x.txt").write_text("source")
            backup = root / "backup"
            create_encrypted_backup(source, backup, "a sufficiently long passphrase")
            destination = root / "restore"
            destination.mkdir()
            (destination / "keep.txt").write_text("keep")
            with self.assertRaises(BackupError):
                restore_encrypted_backup(backup, destination, "a sufficiently long passphrase")
            self.assertEqual((destination / "keep.txt").read_text(), "keep")

    def test_sqlite_is_snapshotted_and_integrity_checked(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "data"
            source.mkdir()
            db = sqlite3.connect(source / "tasks.sqlite3")
            db.execute("CREATE TABLE tasks (id INTEGER PRIMARY KEY, name TEXT)")
            db.execute("INSERT INTO tasks(name) VALUES ('preserved')")
            db.commit()
            db.close()
            # A stale WAL sidecar must not be restored beside the SQLite backup snapshot.
            (source / "tasks.sqlite3-wal").write_bytes(b"stale-wal")
            backup = root / "backup"
            create_encrypted_backup(source, backup, "a sufficiently long passphrase")
            destination = root / "restore"
            restore_encrypted_backup(backup, destination, "a sufficiently long passphrase")
            restored = sqlite3.connect(destination / "tasks.sqlite3")
            self.assertEqual(restored.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(restored.execute("SELECT name FROM tasks").fetchone()[0], "preserved")
            restored.close()
            self.assertFalse((destination / "tasks.sqlite3-wal").exists())

    def test_rejects_cross_platform_archive_path_traversal(self):
        from ui.desktop.backup import _safe_archive_path
        for name in ("../escape", "..\\\\escape", "C:/Windows/system.ini", "/absolute/path", "folder/./file", "CON", "nul.txt", "LPT1.log", "trailing. ", "name:stream", "bad?.txt"):
            with self.subTest(name=name):
                with self.assertRaises(BackupError):
                    _safe_archive_path(name)

    def test_rejects_case_colliding_archive_paths(self):
        from ui.desktop.backup import _safe_archive_path
        paths = ("Config.json", "config.json")
        normalized = set()
        for raw in paths:
            portable = "/".join(part.casefold() for part in _safe_archive_path(raw).parts)
            self.assertNotIn(portable, normalized)
            normalized.add(portable)

    def test_rejects_symbolic_link_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "data"
            source.mkdir()
            link = root / "link"
            try:
                link.symlink_to(source, target_is_directory=True)
            except OSError:
                self.skipTest("symlinks unavailable")
            with self.assertRaises(BackupError):
                create_encrypted_backup(link, root / "backup", "a sufficiently long passphrase")


if __name__ == "__main__":
    unittest.main()
