import json
from contextlib import closing
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from core.backup_inventory import SQLiteDatabaseEntry
from core.sqlite_backup_set import create_sqlite_backup_set
from core.sqlite_backup_verify import SQLiteBackupVerificationError, verify_sqlite_backup_set


class SQLiteBackupVerificationTests(unittest.TestCase):
    def _db(self, path, value):
        with closing(sqlite3.connect(path)) as db:
            db.execute("CREATE TABLE records(value TEXT NOT NULL)")
            db.execute("INSERT INTO records VALUES (?)", (value,))
            db.commit()

    def _fixture(self):
        temporary = tempfile.TemporaryDirectory()
        base = Path(temporary.name)
        workspace = base / "workspace"
        workspace.mkdir()
        backup_root = base / "backups"
        backup_root.mkdir(mode=0o700)
        if os.name == "posix":
            os.chmod(backup_root, 0o700)
        source = workspace / "auth.sqlite3"
        self._db(source, "verified")
        entries = (SQLiteDatabaseEntry("authentication", source),)
        backup_set = create_sqlite_backup_set(entries, backup_root, workspace_root=workspace)
        return temporary, entries, backup_set

    def test_verifies_valid_set_and_returns_manifest(self):
        temporary, entries, backup_set = self._fixture()
        with temporary:
            manifest = verify_sqlite_backup_set(backup_set, entries)
            self.assertEqual(manifest["set_id"], backup_set.name)
            self.assertEqual(manifest["databases"][0]["name"], "authentication")

    def test_rejects_modified_snapshot_digest(self):
        temporary, entries, backup_set = self._fixture()
        with temporary:
            snapshot = backup_set / "authentication.sqlite3"
            snapshot.write_bytes(snapshot.read_bytes() + b"tampered")
            with self.assertRaisesRegex(SQLiteBackupVerificationError, "backup_size_mismatch|backup_digest_mismatch"):
                verify_sqlite_backup_set(backup_set, entries)

    def test_rejects_unexpected_file(self):
        temporary, entries, backup_set = self._fixture()
        with temporary:
            (backup_set / "unexpected.txt").write_text("extra", encoding="utf-8")
            with self.assertRaisesRegex(SQLiteBackupVerificationError, "backup_set_file_inventory_mismatch"):
                verify_sqlite_backup_set(backup_set, entries)

    def test_rejects_manifest_path_traversal(self):
        temporary, entries, backup_set = self._fixture()
        with temporary:
            path = backup_set / "manifest.json"
            manifest = json.loads(path.read_text(encoding="utf-8"))
            manifest["databases"][0]["filename"] = "../authentication.sqlite3"
            path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(SQLiteBackupVerificationError, "manifest_filename_invalid"):
                verify_sqlite_backup_set(backup_set, entries)

    def test_rejects_duplicate_json_keys(self):
        temporary, entries, backup_set = self._fixture()
        with temporary:
            path = backup_set / "manifest.json"
            path.write_text('{"format_version":1,"format_version":1}', encoding="utf-8")
            with self.assertRaisesRegex(SQLiteBackupVerificationError, "manifest_duplicate_json_key"):
                verify_sqlite_backup_set(backup_set, entries)

    def test_rejects_missing_expected_database(self):
        temporary, entries, backup_set = self._fixture()
        with temporary:
            other = (SQLiteDatabaseEntry("workforce", entries[0].path),)
            with self.assertRaisesRegex(SQLiteBackupVerificationError, "manifest_database_inventory_mismatch"):
                verify_sqlite_backup_set(backup_set, other)

    def test_rejects_symlinked_snapshot(self):
        if not hasattr(os, "symlink"):
            self.skipTest("symlinks unavailable")
        temporary, entries, backup_set = self._fixture()
        with temporary:
            snapshot = backup_set / "authentication.sqlite3"
            actual = backup_set / "actual.sqlite3"
            snapshot.rename(actual)
            try:
                snapshot.symlink_to(actual)
            except OSError:
                self.skipTest("symlink creation not permitted")
            with self.assertRaisesRegex(SQLiteBackupVerificationError, "backup_file_symlink_not_allowed"):
                verify_sqlite_backup_set(backup_set, entries)

    def test_rejects_non_uuid_directory_name(self):
        temporary, entries, backup_set = self._fixture()
        with temporary:
            renamed = backup_set.parent / "not-a-set-id"
            backup_set.rename(renamed)
            with self.assertRaisesRegex(SQLiteBackupVerificationError, "invalid_backup_set_directory"):
                verify_sqlite_backup_set(renamed, entries)


if __name__ == "__main__":
    unittest.main()
