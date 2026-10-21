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
            # Only the execution supervisor can assert that the process has
            # stopped; startup recovery must not guess from a "running" row.
            self.journal.transition(execution_id, "cleanup_required")

    def test_startup_recovery_does_not_reclassify_live_running_execution(self):
        self.journal.create("live-run", self.context | {"execution_marker": "live"})
        self.journal.transition("live-run", "running")

        discovered = self.journal.mark_interrupted_for_reconciliation()

        self.assertEqual(discovered, ())
        self.assertEqual(self.journal.get("live-run").state, "running")

    def test_startup_recovery_resumes_explicit_cleanup_required_execution(self):
        self.journal.create("pending-run", self.context | {"execution_marker": "pending"})
        self.journal.transition("pending-run", "running")
        self.journal.transition("pending-run", "cleanup_required")

        discovered = self.journal.mark_interrupted_for_reconciliation()

        self.assertEqual(discovered, ("pending-run",))
        self.assertEqual(self.journal.get("pending-run").state, "cleanup_required")

    def test_live_claim_by_other_worker_skips_cleanup(self):
        self.create_pending("run-claimed")
        self.assertTrue(self.journal.claim_recovery("run-claimed", "worker-owner", 60))
        reconcile = Mock(return_value=ContainerRecoveryResult("run-claimed", False, False, True))
        report = recover_interrupted_executions(
            self.journal, reconcile=reconcile, worker_id="worker-contender"
        )
        self.assertEqual(report.discovered, 1)
        self.assertEqual(report.cleaned, 0)
        self.assertEqual(report.pending, 1)
        self.assertEqual(report.items[0].outcome, "claimed_by_other_worker")
        reconcile.assert_not_called()
        self.assertEqual(self.journal.get("run-claimed").state, "cleanup_required")

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

    def test_cleanup_before_journal_finalize_is_retried_idempotently(self):
        self.create_pending("run-crash-window")
        physically_removed = ContainerRecoveryResult(
            "run-crash-window", inspected=True, removed=True, already_absent=False
        )

        def cleanup_then_lose_claim(execution_id):
            self.assertTrue(self.journal.release_recovery_claim(execution_id, "recovery-test"))
            return physically_removed

        first = recover_interrupted_executions(
            self.journal,
            reconcile=cleanup_then_lose_claim,
            worker_id="recovery-test",
        )
        self.assertEqual((first.cleaned, first.pending), (0, 1))
        record = self.journal.get("run-crash-window")
        self.assertEqual(record.state, "cleanup_required")
        self.assertFalse(record.cleanup_confirmed)

        # The next pass sees an absent container and can safely finalize the journal.
        absent = ContainerRecoveryResult(
            "run-crash-window", inspected=False, removed=False, already_absent=True
        )
        second = recover_interrupted_executions(
            self.journal,
            reconcile=Mock(return_value=absent),
            worker_id="recovery-next",
        )
        self.assertEqual((second.cleaned, second.pending), (1, 0))
        record = self.journal.get("run-crash-window")
        self.assertEqual(record.state, "cleaned")
        self.assertTrue(record.cleanup_confirmed)


if __name__ == "__main__":
    unittest.main()
