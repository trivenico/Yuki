import sqlite3
from contextlib import closing
import tempfile
import unittest
from pathlib import Path

from core.execution_journal import SQLiteExecutionJournal
from core.journal_backup import JournalBackupError, backup_sqlite_database


class JournalBackupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.source = root / "journal.sqlite3"
        self.destination = root / "backups" / "journal.backup.sqlite3"
        self.journal = SQLiteExecutionJournal(str(self.source))
        record = self.journal.create("backup-job", {"proposal": "safe"})
        self.journal.transition(record.execution_id, "running")

    def test_backup_contains_consistent_journal_state(self):
        result = backup_sqlite_database(self.source, self.destination)
        self.assertEqual(result, self.destination)
        with closing(sqlite3.connect(str(result))) as db:
            self.assertEqual(db.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            row = db.execute(
                "SELECT execution_id, state FROM sandbox_executions WHERE execution_id=?",
                ("backup-job",),
            ).fetchone()
        self.assertEqual(row, ("backup-job", "running"))

    def test_backup_can_replace_existing_destination_atomically(self):
        self.destination.parent.mkdir(parents=True)
        self.destination.write_bytes(b"old backup")
        backup_sqlite_database(self.source, self.destination)
        with closing(sqlite3.connect(str(self.destination))) as db:
            self.assertEqual(db.execute("PRAGMA integrity_check").fetchone()[0], "ok")

    def test_backup_rejects_same_source_and_destination(self):
        with self.assertRaisesRegex(JournalBackupError, "must differ"):
            backup_sqlite_database(self.source, self.source)

    def test_backup_rejects_missing_source_without_touching_destination(self):
        self.destination.parent.mkdir(parents=True)
        self.destination.write_bytes(b"keep existing")
        with self.assertRaises(JournalBackupError):
            backup_sqlite_database(self.source.parent / "missing.sqlite3", self.destination)
        self.assertEqual(self.destination.read_bytes(), b"keep existing")

    def test_invalid_source_does_not_replace_existing_backup(self):
        invalid_source = Path(self.temp.name) / "invalid.sqlite3"
        invalid_source.write_bytes(b"not sqlite")
        self.destination.parent.mkdir(parents=True)
        self.destination.write_bytes(b"keep existing")
        with self.assertRaises(JournalBackupError):
            backup_sqlite_database(invalid_source, self.destination)
        self.assertEqual(self.destination.read_bytes(), b"keep existing")


if __name__ == "__main__":
    unittest.main()
