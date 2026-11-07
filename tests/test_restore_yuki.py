"""Tests for safe, verified Yuki SQLite backup restoration."""
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from scripts.backup_yuki import create_backup
from scripts.restore_yuki import restore_backup


class RestoreYukiTests(unittest.TestCase):
    def _backup(self, root):
        workspace = root / "workspace"
        workspace.mkdir()
        database = workspace / "nested" / "memory.sqlite3"
        database.parent.mkdir()
        with sqlite3.connect(database) as db:
            db.execute("CREATE TABLE memories (value TEXT)")
            db.execute("INSERT INTO memories VALUES ('restore me')")
        backup = root / "backup"
        create_backup(workspace, backup)
        return workspace, backup

    def test_restore_verifies_and_recovers_database(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _, backup = self._backup(root)
            destination = root / "restored"
            report = restore_backup(backup, destination)
            self.assertTrue(report["verified"])
            self.assertEqual(report["database_count"], 1)
            with sqlite3.connect(destination / "nested" / "memory.sqlite3") as db:
                self.assertEqual(db.execute("SELECT value FROM memories").fetchone()[0], "restore me")

    def test_restore_rejects_database_symlink(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _, backup = self._backup(root)
            manifest = json.loads((backup / "manifest.json").read_text())
            entry = manifest["databases"][0]
            linked = backup / entry["backup_path"]
            original = linked.read_bytes()
            outside = root / "outside.sqlite3"
            outside.write_bytes(original)
            linked.unlink()
            try:
                linked.symlink_to(outside)
            except (OSError, NotImplementedError):
                self.skipTest("Symbolic links are unavailable on this platform")
            with self.assertRaisesRegex(ValueError, "must not contain symbolic links"):
                restore_backup(backup, root / "restored")

    def test_restore_rejects_symlinked_parent_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _, backup = self._backup(root)
            databases = backup / "databases"
            outside = root / "outside"
            databases.rename(outside)
            try:
                databases.symlink_to(outside, target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest("Directory symbolic links are unavailable on this platform")
            with self.assertRaisesRegex(ValueError, "paths must not contain symbolic links"):
                restore_backup(backup, root / "restored")

    def test_restore_rejects_manifest_symlink(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _, backup = self._backup(root)
            manifest = backup / "manifest.json"
            outside = root / "manifest-copy.json"
            outside.write_bytes(manifest.read_bytes())
            manifest.unlink()
            try:
                manifest.symlink_to(outside)
            except (OSError, NotImplementedError):
                self.skipTest("Symbolic links are unavailable on this platform")
            with self.assertRaisesRegex(ValueError, "manifest must not be a symbolic link"):
                restore_backup(backup, root / "restored")

    def test_restore_refuses_existing_destination(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _, backup = self._backup(root)
            destination = root / "existing"
            destination.mkdir()
            with self.assertRaisesRegex(FileExistsError, "refusing to overwrite"):
                restore_backup(backup, destination)

    def test_restore_rejects_checksum_tampering(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _, backup = self._backup(root)
            entry = json.loads((backup / "manifest.json").read_text())["databases"][0]
            (backup / entry["backup_path"]).write_bytes(b"tampered")
            with self.assertRaisesRegex(ValueError, "checksum or size"):
                restore_backup(backup, root / "restored")

    def test_restore_rejects_path_traversal(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _, backup = self._backup(root)
            manifest_path = backup / "manifest.json"
            manifest = json.loads(manifest_path.read_text())
            manifest["databases"][0]["backup_path"] = "../outside.db"
            manifest_path.write_text(json.dumps(manifest))
            with self.assertRaisesRegex(ValueError, "unsafe relative path"):
                restore_backup(backup, root / "restored")


    def test_restore_refuses_destination_created_during_staging(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _, backup = self._backup(root)
            destination = root / "restored"
            import scripts.restore_yuki as restore_module
            original_copy = restore_module.shutil.copy2

            def create_destination_during_copy(source, target):
                result = original_copy(source, target)
                destination.mkdir()
                (destination / "keep.txt").write_text("do not overwrite", encoding="utf-8")
                return result

            with patch.object(restore_module.shutil, "copy2", side_effect=create_destination_during_copy):
                with self.assertRaises(FileExistsError):
                    restore_backup(backup, destination)
            self.assertEqual((destination / "keep.txt").read_text(encoding="utf-8"), "do not overwrite")
            self.assertEqual(list(root.glob(".restored.restore-*")), [])

    def test_restore_rejects_corrupt_manifest_format(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _, backup = self._backup(root)
            manifest_path = backup / "manifest.json"
            manifest = json.loads(manifest_path.read_text())
            manifest["format"] = "unknown"
            manifest_path.write_text(json.dumps(manifest))
            with self.assertRaisesRegex(ValueError, "Unsupported"):
                restore_backup(backup, root / "restored")


if __name__ == "__main__":
    unittest.main()
