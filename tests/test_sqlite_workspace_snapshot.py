import sqlite3
import tempfile
import unittest
from pathlib import Path

from scripts.sqlite_workspace_snapshot import SnapshotError, snapshot_workspace


class SQLiteWorkspaceSnapshotTests(unittest.TestCase):
    def test_online_backup_includes_committed_wal_data_and_copies_other_files(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "workspace"
            source.mkdir()
            database = source / "agent_memory.sqlite3"
            live = sqlite3.connect(database)
            try:
                live.execute("PRAGMA journal_mode=WAL")
                live.execute("PRAGMA wal_autocheckpoint=0")
                live.execute("CREATE TABLE memories (content TEXT NOT NULL)")
                live.execute("INSERT INTO memories VALUES (?)", ("committed while WAL is active",))
                live.commit()
                self.assertTrue(Path(str(database) + "-wal").exists())

                (source / "settings.json").write_text('{"theme":"dark"}', encoding="utf-8")
                nested = source / "nested"
                nested.mkdir()
                (nested / "note.txt").write_text("preserve", encoding="utf-8")
                target = root / "snapshot"

                result = snapshot_workspace(source, target)
                self.assertEqual(result["sqlite_databases_snapshotted"], 1)
                self.assertEqual(result["files_copied"], 2)
                self.assertFalse(Path(str(target / "agent_memory.sqlite3") + "-wal").exists())
                self.assertFalse(Path(str(target / "agent_memory.sqlite3") + "-shm").exists())
                self.assertEqual((target / "settings.json").read_text(encoding="utf-8"), '{"theme":"dark"}')
                self.assertEqual((target / "nested" / "note.txt").read_text(encoding="utf-8"), "preserve")
                with sqlite3.connect(target / "agent_memory.sqlite3") as restored:
                    self.assertEqual(
                        restored.execute("SELECT content FROM memories").fetchone()[0],
                        "committed while WAL is active",
                    )
                    self.assertEqual(restored.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            finally:
                live.close()

    def test_rejects_existing_or_nested_snapshot_destination(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "workspace"
            source.mkdir()
            existing = root / "existing"
            existing.mkdir()
            with self.assertRaises(SnapshotError):
                snapshot_workspace(source, existing)
            with self.assertRaises(SnapshotError):
                snapshot_workspace(source, source / "snapshot")
            with self.assertRaises(SnapshotError):
                snapshot_workspace(source, root)

    def test_rejects_symlinks_in_workspace(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "workspace"
            source.mkdir()
            external = root / "outside.txt"
            external.write_text("outside", encoding="utf-8")
            link = source / "external.txt"
            try:
                link.symlink_to(external)
            except (OSError, NotImplementedError):
                self.skipTest("Symlink creation is not available on this platform")
            with self.assertRaises(SnapshotError):
                snapshot_workspace(source, root / "snapshot")
            self.assertFalse((root / "snapshot").exists())

    def test_corrupt_sqlite_snapshot_fails_and_cleans_staging_directory(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "workspace"
            source.mkdir()
            (source / "broken.sqlite3").write_bytes(b"SQLite format 3\\x00" + b"corrupt")
            target = root / "snapshot"
            with self.assertRaises(SnapshotError):
                snapshot_workspace(source, target)
            self.assertFalse(target.exists())


if __name__ == "__main__":
    unittest.main()
