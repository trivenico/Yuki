from contextlib import closing
"""Integration tests for atomic ledger recovery and dispatch requeue."""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from automation.reviewer_contracts import ReviewTask
from automation.reviewer_ledger import ReviewerTaskLedger
from automation.reviewer_leases import ReviewerLeaseAuthority
from automation.reviewer_sessions import ReviewerSessionAuthority
from automation.reviewer_signing import HMACReviewerSigner


class ReviewerLedgerRecoveryIntegrationTests(unittest.TestCase):
    def test_recovery_rotates_lease_and_requeues_dispatch_for_replacement(self):
        with tempfile.TemporaryDirectory() as tmp:
            database = str(Path(tmp) / "ledger.sqlite")
            sessions = ReviewerSessionAuthority(
                Path(tmp) / "sessions.sqlite", secret=b"s" * 32,
                authorize=lambda *_: True,
            )
            leases = ReviewerLeaseAuthority(
                database, secret=b"l" * 32, authorize=lambda *_: True,
            )
            signer = HMACReviewerSigner(key_id="review-key", key=b"k" * 32)
            ledger = ReviewerTaskLedger(
                database, authorize=lambda *_: True,
                signer_resolver=lambda *_: signer,
                session_authority=sessions, lease_authority=leases,
            )
            task = ReviewTask(
                task_id="recovery-task", artifact_sha256="a" * 64,
                author_agent_id="builder", reviewer_agent_id="security-reviewer",
                check_name="security",
                requirements=("Inspect authorization checks",),
                evidence_references=("snapshot://artifact/source",),
            )
            created = ledger.create_tasks_with_leases(
                actor_id="supervisor", tasks=(task,), lease_ttl_seconds=30,
            )[0]
            session = sessions.issue(
                reviewer_agent_id=task.reviewer_agent_id,
                artifact_sha256=task.artifact_sha256,
            )
            ledger.claim(
                actor_id=task.reviewer_agent_id, task_id=task.task_id,
                expected_version=0, session_token=session,
                operation_id="claim-before-recovery",
                lease_token=created["lease_token"],
                lease_generation=created["lease_generation"],
            )
            with closing(sqlite3.connect(database)) as db, db:
                db.execute(
                    "UPDATE reviewer_leases SET expires_at='2000-01-01T00:00:00+00:00' WHERE task_id=?",
                    (task.task_id,),
                )

            recovered = ledger.recover_in_progress(
                actor_id="recovery-controller", task_id=task.task_id,
                expected_version=1, replacement_reviewer_agent_id="replacement-reviewer",
                reason="worker process heartbeat expired",
            )
            self.assertEqual(recovered["state"], "assigned")
            self.assertEqual(recovered["lease_generation"], created["lease_generation"] + 1)
            self.assertNotEqual(recovered["lease_token"], created["lease_token"])
            record = ledger.get(actor_id="auditor", task_id=task.task_id)
            self.assertEqual(record["reviewer_agent_id"], "replacement-reviewer")
            self.assertEqual([event["type"] for event in record["events"]][-2:],
                             ["abandoned", "reassigned"])

            dispatch = ledger.claim_dispatches(
                actor_id="replacement-reviewer", worker_id="replacement-reviewer",
                limit=1,
            )
            self.assertEqual(len(dispatch), 1)
            self.assertEqual(dispatch[0]["task_id"], task.task_id)
            self.assertEqual(dispatch[0]["reviewer_agent_id"], "replacement-reviewer")


if __name__ == "__main__":
    unittest.main()
