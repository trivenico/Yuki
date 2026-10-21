import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from pathlib import Path

from core.execution_journal import ExecutionJournalError, SQLiteExecutionJournal


class ExecutionJournalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.journal = SQLiteExecutionJournal(str(Path(self.temp.name) / "jobs.sqlite3"))
        self.context = {
            "proposal_sha256": "a" * 64,
            "image_digest": "sha256:" + "b" * 64,
            "policy": {"network_mode": "none", "capabilities": []},
            "commands": [["pytest", "-q"]],
        }

    def test_recovery_claim_excludes_competing_worker_and_releases_owner(self):
        record = self.journal.create("claim-job", {"proposal": "x"})
        self.journal.transition(record.execution_id, "running")
        self.journal.transition(record.execution_id, "cleanup_required")
        self.assertTrue(self.journal.claim_recovery("claim-job", "worker-a", 60))
        self.assertFalse(self.journal.claim_recovery("claim-job", "worker-b", 60))
        self.assertFalse(self.journal.release_recovery_claim("claim-job", "worker-b"))
        self.assertTrue(self.journal.release_recovery_claim("claim-job", "worker-a"))
        self.assertTrue(self.journal.claim_recovery("claim-job", "worker-b", 60))

    def test_concurrent_journal_instances_grant_only_one_recovery_claim(self):
        record = self.journal.create("race-job", {"proposal": "x"})
        self.journal.transition(record.execution_id, "running")
        self.journal.transition(record.execution_id, "cleanup_required")
        second_journal = SQLiteExecutionJournal(str(Path(self.temp.name) / "jobs.sqlite3"))
        barrier = Barrier(2)
        def claim(journal, worker):
            barrier.wait(timeout=5)
            return journal.claim_recovery(record.execution_id, worker, 60)
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(claim, self.journal, "worker-a"),
                pool.submit(claim, second_journal, "worker-b"),
            ]
            results = [future.result(timeout=10) for future in futures]
        self.assertEqual(sorted(results), [False, True])

    def test_recovery_claim_renewal_requires_current_unexpired_owner(self):
        record = self.journal.create("renew-job", {"proposal": "x"})
        self.journal.transition(record.execution_id, "running")
        self.journal.transition(record.execution_id, "cleanup_required")
        self.assertTrue(self.journal.claim_recovery(record.execution_id, "worker-a", 60))
        self.assertTrue(self.journal.renew_recovery_claim(record.execution_id, "worker-a", 120))
        self.assertFalse(self.journal.renew_recovery_claim(record.execution_id, "worker-b", 120))
        with self.assertRaises(ExecutionJournalError):
            self.journal.renew_recovery_claim(record.execution_id, "worker-a", 1)

    def test_recovery_claim_rejects_invalid_lease(self):
        with self.assertRaises(ExecutionJournalError):
            self.journal.claim_recovery("missing", "worker-a", 1)
    def test_cleanup_finalization_requires_active_claim(self):
        record = self.journal.create("fenced-job", {"proposal": "x"})
        self.journal.transition(record.execution_id, "running")
        self.journal.transition(record.execution_id, "cleanup_required")
        with self.assertRaisesRegex(ExecutionJournalError, "requires the active recovery worker claim"):
            self.journal.transition(record.execution_id, "cleaned", cleanup_confirmed=True)
        self.assertTrue(self.journal.claim_recovery(record.execution_id, "worker-a", 60))
        with self.assertRaisesRegex(ExecutionJournalError, "missing, expired, or owned"):
            self.journal.transition(record.execution_id, "cleaned", cleanup_confirmed=True, recovery_worker_id="worker-b")
        cleaned = self.journal.transition(record.execution_id, "cleaned", cleanup_confirmed=True, recovery_worker_id="worker-a")
        self.assertEqual(cleaned.state, "cleaned")

    def test_create_is_idempotent_for_same_context(self):
        first = self.journal.create("exec-1", self.context)
        second = self.journal.create("exec-1", self.context)
        self.assertEqual(first.context_sha256, second.context_sha256)
        self.assertEqual(second.state, "created")

    def test_idempotency_key_cannot_be_reused_for_different_context(self):
        self.journal.create("exec-1", self.context)
        changed = dict(self.context, proposal_sha256="c" * 64)
        with self.assertRaisesRegex(ExecutionJournalError, "different context"):
            self.journal.create("exec-1", changed)

    def test_state_transitions_are_validated(self):
        self.journal.create("exec-1", self.context)
        self.journal.transition("exec-1", "running")
        succeeded = self.journal.transition(
            "exec-1", "succeeded", result={"returncode": 0}, cleanup_confirmed=True
        )
        self.assertEqual(succeeded.state, "succeeded")
        self.assertTrue(succeeded.cleanup_confirmed)
        with self.assertRaisesRegex(ExecutionJournalError, "invalid transition"):
            self.journal.transition("exec-1", "running")

    def test_interrupted_jobs_are_marked_for_reconciliation_not_replayed(self):
        self.journal.create("exec-1", self.context)
        self.journal.create("exec-2", dict(self.context, proposal_sha256="c" * 64))
        self.journal.transition("exec-2", "running")
        self.journal.transition("exec-2", "cleanup_required")
        marked = self.journal.mark_interrupted_for_reconciliation()
        self.assertEqual(set(marked), {"exec-1", "exec-2"})
        self.assertEqual(self.journal.get("exec-1").state, "cleanup_required")
        self.assertEqual(self.journal.get("exec-2").state, "cleanup_required")
        # Pending cleanup remains discoverable so a restarted recovery worker can resume.
        self.assertEqual(set(self.journal.mark_interrupted_for_reconciliation()), {"exec-1", "exec-2"})

    def test_result_size_and_non_json_values_are_rejected(self):
        self.journal.create("exec-1", self.context)
        self.journal.transition("exec-1", "running")
        with self.assertRaises(ExecutionJournalError):
            self.journal.transition("exec-1", "failed", result={"not_json": object()})
        with self.assertRaisesRegex(ExecutionJournalError, "64 KiB"):
            self.journal.transition("exec-1", "failed", result={"output": "x" * 70000})
        self.assertEqual(self.journal.get("exec-1").state, "running")

    def test_unknown_job_and_invalid_context_are_rejected(self):
        with self.assertRaises(ExecutionJournalError):
            self.journal.create("exec-1", {"bad": float("nan")})
        with self.assertRaisesRegex(ExecutionJournalError, "not found"):
            self.journal.get("missing")


if __name__ == "__main__":
    unittest.main()
