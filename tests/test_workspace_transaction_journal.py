import hashlib
import os
import tempfile
import unittest
from pathlib import Path

from automation.workspace_transaction_journal import (
    WorkspaceJournalError,
    WorkspaceTransactionJournal,
)


class WorkspaceTransactionJournalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "workspace"
        self.root.mkdir()
        self.journal_root = Path(self.temp.name) / "private-journal"
        self.one = self.root / "one.txt"
        self.two = self.root / "two.txt"
        self.one.write_bytes(b"one-old")
        self.two.write_bytes(b"two-old")
        self.journal = WorkspaceTransactionJournal(self.root, self.journal_root)

    def prepare(self):
        changes = [
            ("one.txt", self.one, b"one-old", b"one-new"),
            ("two.txt", self.two, b"two-old", b"two-new"),
        ]
        digest = hashlib.sha256(b"operation").hexdigest()
        return self.journal.begin(actor_id="owner:test", operation_digest=digest, changes=changes)

    def test_recovery_rolls_back_crash_after_first_replace_and_is_idempotent(self):
        manifest = self.prepare()
        self.journal.mark_applying(manifest["operation_id"])
        os.replace(manifest["entries"][0]["stage"], self.one)
        # Simulate process death before recording the first replacement.
        self.assertEqual(self.journal.recover_pending(), [manifest["operation_id"]])
        self.assertEqual(self.one.read_bytes(), b"one-old")
        self.assertEqual(self.two.read_bytes(), b"two-old")
        self.assertEqual(self.journal.recover_pending(), [])

    def test_recovery_detects_external_conflict_instead_of_overwriting_it(self):
        manifest = self.prepare()
        self.journal.mark_applying(manifest["operation_id"])
        os.replace(manifest["entries"][0]["stage"], self.one)
        self.two.write_bytes(b"external-change")
        with self.assertRaises(WorkspaceJournalError):
            self.journal.recover_pending()
        operation_dir = self.journal.directory / manifest["operation_id"]
        self.assertEqual(self.journal._load(operation_dir)["state"], "recovery_required")
        self.assertEqual(self.two.read_bytes(), b"external-change")

    def test_committed_operation_is_never_rolled_back(self):
        manifest = self.prepare()
        self.journal.mark_applying(manifest["operation_id"])
        for entry in manifest["entries"]:
            os.replace(entry["stage"], Path(entry["target"]))
        self.journal.mark_committed(manifest["operation_id"])
        self.assertEqual(self.journal.recover_pending(), [])
        self.assertEqual(self.one.read_bytes(), b"one-new")
        self.assertEqual(self.two.read_bytes(), b"two-new")

    def test_journal_directory_must_be_outside_workspace(self):
        with self.assertRaises(WorkspaceJournalError):
            WorkspaceTransactionJournal(self.root, self.root / ".journal")

    def test_backup_corruption_fails_closed(self):
        manifest = self.prepare()
        backup = self.journal.directory / manifest["operation_id"] / "backup-00.bin"
        backup.write_bytes(b"tampered")
        with self.assertRaises(WorkspaceJournalError):
            self.journal.recover_pending()


if __name__ == "__main__":
    unittest.main()
