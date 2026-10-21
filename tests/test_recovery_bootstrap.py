import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from core.execution_journal import SQLiteExecutionJournal
from core.recovery_bootstrap import JOURNAL_FILENAME, recover_workspace_on_startup
from core.startup_recovery import StartupRecoveryReport


class RecoveryBootstrapTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.workspace = Path(self.temp.name)

    def test_no_journal_does_not_create_one_or_call_docker(self):
        with patch("core.recovery_bootstrap.recover_interrupted_executions") as recover:
            self.assertIsNone(recover_workspace_on_startup(self.workspace))
        recover.assert_not_called()
        self.assertFalse((self.workspace / JOURNAL_FILENAME).exists())

    def test_existing_journal_invokes_recovery_before_runtime(self):
        journal_path = self.workspace / JOURNAL_FILENAME
        journal = SQLiteExecutionJournal(str(journal_path))
        context = {"proposal_sha256": "a" * 64, "execution_marker": "interrupted"}
        journal.create("run-1", context)
        journal.transition("run-1", "running")
        with patch(
            "core.recovery_bootstrap.recover_interrupted_executions",
            return_value=StartupRecoveryReport(1, 0, 1, ()),
        ) as recover:
            report = recover_workspace_on_startup(self.workspace)
        self.assertEqual(report.pending, 1)
        recover.assert_called_once()

    def test_symlink_journal_is_rejected(self):
        target = self.workspace / "target.sqlite3"
        SQLiteExecutionJournal(str(target))
        link = self.workspace / JOURNAL_FILENAME
        try:
            link.symlink_to(target)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks are not available on this platform")
        with self.assertRaises(RuntimeError):
            recover_workspace_on_startup(self.workspace)


if __name__ == "__main__":
    unittest.main()
