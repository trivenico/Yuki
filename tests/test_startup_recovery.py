import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from core.container_recovery import ContainerRecoveryError, ContainerRecoveryResult
from core.execution_journal import SQLiteExecutionJournal
from core.startup_recovery import recover_interrupted_executions


class StartupRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.journal = SQLiteExecutionJournal(str(Path(self.temp.name) / "journal.sqlite3"))
        self.context = {
            "proposal_sha256": "a" * 64,
            "image_digest": "sha256:" + "b" * 64,
            "policy": {"network_mode": "none"},
            "commands": [["pytest", "-q"]],
        }

    def create_pending(self, execution_id, state="running"):
        self.journal.create(execution_id, self.context | {"execution_marker": execution_id})
        if state == "running":
            self.journal.transition(execution_id, "running")
        self.journal.mark_interrupted_for_reconciliation()

    def test_successful_cleanup_is_persisted(self):
        self.create_pending("run-1")
        reconcile = Mock(return_value=ContainerRecoveryResult("run-1", True, True, False))
        report = recover_interrupted_executions(self.journal, reconcile=reconcile)
        self.assertEqual((report.discovered, report.cleaned, report.pending), (1, 1, 0))
        self.assertEqual(self.journal.get("run-1").state, "cleaned")
        self.assertTrue(self.journal.get("run-1").cleanup_confirmed)
        reconcile.assert_called_once_with("run-1")

    def test_already_absent_container_is_idempotent_cleanup(self):
        self.create_pending("run-1")
        reconcile = Mock(return_value=ContainerRecoveryResult("run-1", False, False, True))
        report = recover_interrupted_executions(self.journal, reconcile=reconcile)
        self.assertEqual(report.cleaned, 1)
        self.assertEqual(self.journal.get("run-1").state, "cleaned")

    def test_ambiguous_docker_state_stays_pending_and_is_retried_next_start(self):
        self.create_pending("run-1")
        first = recover_interrupted_executions(
            self.journal,
            reconcile=Mock(side_effect=ContainerRecoveryError("daemon unavailable")),
        )
        self.assertEqual(first.pending, 1)
        self.assertEqual(self.journal.get("run-1").state, "cleanup_required")
        reconcile = Mock(return_value=ContainerRecoveryResult("run-1", False, False, True))
        second = recover_interrupted_executions(self.journal, reconcile=reconcile)
        self.assertEqual(second.cleaned, 1)
        reconcile.assert_called_once_with("run-1")

    def test_never_replays_execution_or_touches_terminal_jobs(self):
        self.create_pending("run-1")
        self.journal.create("done-1", self.context | {"execution_marker": "done"})
        self.journal.transition("done-1", "running")
        self.journal.transition("done-1", "succeeded", result={"returncode": 0}, cleanup_confirmed=True)
        reconcile = Mock(return_value=ContainerRecoveryResult("run-1", False, False, True))
        report = recover_interrupted_executions(self.journal, reconcile=reconcile)
        self.assertEqual(report.discovered, 1)
        self.assertEqual(self.journal.get("done-1").state, "succeeded")
        reconcile.assert_called_once_with("run-1")


if __name__ == "__main__":
    unittest.main()
