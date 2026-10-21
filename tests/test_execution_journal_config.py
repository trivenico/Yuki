import tempfile
import unittest
from pathlib import Path

from core.execution_journal_config import JOURNAL_FILENAME, execution_journal_path, open_execution_journal


class ExecutionJournalConfigTests(unittest.TestCase):
    def test_path_is_canonical_under_workspace(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.assertEqual(execution_journal_path(root), root / JOURNAL_FILENAME)

    def test_non_directory_workspace_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "file"
            path.write_text("x", encoding="utf-8")
            with self.assertRaises(ValueError):
                execution_journal_path(path)

    def test_factory_uses_canonical_path(self):
        with tempfile.TemporaryDirectory() as temp:
            journal = open_execution_journal(temp)
            self.assertEqual(Path(journal.database_path), Path(temp) / JOURNAL_FILENAME)
            record = journal.create("factory-test", {"source": "test"})
            self.assertEqual(journal.get(record.execution_id).state, "created")

    def test_missing_workspace_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(ValueError):
                execution_journal_path(Path(temp) / "missing")


if __name__ == "__main__":
    unittest.main()
