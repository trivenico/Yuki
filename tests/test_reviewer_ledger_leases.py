import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from automation.reviewer_contracts import build_review_result
from automation.reviewer_ledger import ReviewerTaskLedger
from automation.reviewer_leases import ReviewerLeaseAuthority
from automation.reviewer_sessions import ReviewerSessionAuthority
from automation.reviewer_signing import HMACReviewerSigner
from automation.reviewer_team import ReviewerAssignment, ReviewerTeamPlan
from automation.reviewer_workflow import build_review_tasks


class ReviewerLedgerLeaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp.name) / "reviewer.sqlite"
        digest = "a" * 64
        self.sessions = ReviewerSessionAuthority(
            Path(self.temp.name) / "sessions.sqlite", secret=b"s" * 32,
            authorize=lambda actor, action, artifact: (
                actor == "reviewer-1" and artifact == digest
            ),
        )
        self.leases = ReviewerLeaseAuthority(
            self.db_path, secret=b"l" * 32,
            authorize=lambda actor, action, artifact: (
                actor == "supervisor" and artifact == digest
                and action in {"review:lease:acquire", "review:lease:recover"}
            ),
        )
        self.signer = HMACReviewerSigner(key_id="key-1", key=b"k" * 32)
        self.ledger = ReviewerTaskLedger(
            self.db_path,
            authorize=lambda actor, action, artifact: artifact == digest,
            signer_resolver=lambda actor, key_id: self.signer,
            session_authority=self.sessions,
            lease_authority=self.leases,
        )
        plan = ReviewerTeamPlan(
            artifact_sha256=digest, author_agent_id="author",
            assignments=(ReviewerAssignment("tests", "reviewer-1"),),
        )
        self.task = build_review_tasks(
            plan, requirements_by_check={"tests": ("Inspect test coverage",)},
            evidence_references_by_check={"tests": ("snapshot://artifact/tests",)},
        )[0]
        self.ledger.create_tasks(actor_id="supervisor", tasks=(self.task,))
        self.session = self.sessions.issue(
            reviewer_agent_id="reviewer-1", artifact_sha256=digest,
        )
        self.lease = self.leases.acquire(
            actor_id="supervisor", task_id=self.task.task_id,
            artifact_sha256=digest, reviewer_agent_id="reviewer-1",
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_claim_requires_current_lease(self):
        with self.assertRaises(PermissionError):
            self.ledger.claim(
                actor_id="reviewer-1", task_id=self.task.task_id,
                expected_version=0, session_token=self.session,
                operation_id="claim-no-lease",
            )
        claimed = self.ledger.claim(
            actor_id="reviewer-1", task_id=self.task.task_id,
            expected_version=0, session_token=self.session,
            operation_id="claim-with-lease", lease_token=self.lease.token,
            lease_generation=self.lease.generation,
        )
        self.assertEqual(claimed["state"], "in_progress")

    def test_submission_rejects_superseded_generation(self):
        self.ledger.claim(
            actor_id="reviewer-1", task_id=self.task.task_id,
            expected_version=0, session_token=self.session,
            operation_id="claim-before-recovery", lease_token=self.lease.token,
            lease_generation=self.lease.generation,
        )
        expired = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        with sqlite3.connect(self.db_path) as db:
            db.execute("UPDATE reviewer_leases SET expires_at=? WHERE task_id=?",
                       (expired, self.task.task_id))
        replacement = self.leases.recover(
            actor_id="supervisor", task_id=self.task.task_id,
            artifact_sha256="a" * 64, replacement_reviewer_agent_id="reviewer-1",
        )
        result = build_review_result(self.task, status="passed", summary="Coverage is adequate.")
        with self.assertRaises(PermissionError):
            self.ledger.submit(
                actor_id="reviewer-1", task_id=self.task.task_id,
                expected_version=1, result=result, session_token=self.session,
                operation_id="submit-old-generation", lease_token=self.lease.token,
                lease_generation=self.lease.generation,
            )
        submitted = self.ledger.submit(
            actor_id="reviewer-1", task_id=self.task.task_id,
            expected_version=1, result=result, session_token=self.session,
            operation_id="submit-current-generation", lease_token=replacement.token,
            lease_generation=replacement.generation,
        )
        self.assertEqual(submitted["state"], "submitted")


    def test_reassign_atomically_updates_task_and_lease_after_expiry(self):
        expired = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        with sqlite3.connect(self.db_path) as db:
            db.execute("UPDATE reviewer_leases SET expires_at=? WHERE task_id=?",
                       (expired, self.task.task_id))
        result = self.ledger.reassign(
            actor_id="supervisor", task_id=self.task.task_id,
            expected_version=0, replacement_reviewer_agent_id="reviewer-2",
        )
        self.assertEqual(result["reviewer_agent_id"], "reviewer-2")
        self.assertEqual(result["lease_generation"], 2)
        record = self.ledger.get(actor_id="auditor", task_id=self.task.task_id)
        self.assertEqual(record["reviewer_agent_id"], "reviewer-2")
        self.assertEqual(record["state"], "assigned")
        self.assertEqual(record["version"], 1)
        self.assertEqual(record["events"][1]["type"], "reassigned")
        self.assertFalse(self.leases.validate(
            self.lease.token, task_id=self.task.task_id,
            artifact_sha256="a" * 64, reviewer_agent_id="reviewer-1",
            generation=1,
        ))

    def test_reassignment_rejects_active_lease_and_in_progress_task(self):
        with self.assertRaisesRegex(Exception, "only_current_unclaimed_tasks"):
            self.ledger.claim(
                actor_id="reviewer-1", task_id=self.task.task_id,
                expected_version=0, session_token=self.session,
                operation_id="claim-before-reassign", lease_token=self.lease.token,
                lease_generation=self.lease.generation,
            )
            self.ledger.reassign(
                actor_id="supervisor", task_id=self.task.task_id,
                expected_version=1, replacement_reviewer_agent_id="reviewer-2",
            )
        with self.assertRaises(Exception):
            self.ledger.reassign(
                actor_id="supervisor", task_id=self.task.task_id,
                expected_version=0, replacement_reviewer_agent_id="reviewer-2",
            )


    def test_recovers_expired_in_progress_task_atomically(self):
        self.ledger.claim(
            actor_id="reviewer-1", task_id=self.task.task_id,
            expected_version=0, session_token=self.session,
            operation_id="claim-for-recovery", lease_token=self.lease.token,
            lease_generation=self.lease.generation,
        )
        expired = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        with sqlite3.connect(self.db_path) as db:
            db.execute("UPDATE reviewer_leases SET expires_at=? WHERE task_id=?",
                       (expired,self.task.task_id))
        recovered = self.ledger.recover_in_progress(
            actor_id="supervisor", task_id=self.task.task_id,
            expected_version=1, replacement_reviewer_agent_id="reviewer-2",
            reason="Worker heartbeat expired",
        )
        self.assertEqual(recovered["state"],"assigned")
        self.assertEqual(recovered["version"],3)
        self.assertEqual(recovered["lease_generation"],2)
        with sqlite3.connect(self.db_path) as db:
            row=db.execute("SELECT state,reviewer_agent_id,version FROM reviewer_tasks WHERE task_id=?",
                           (self.task.task_id,)).fetchone()
        self.assertEqual(row,("assigned","reviewer-2",3))
        self.assertFalse(self.leases.validate(
            self.lease.token,task_id=self.task.task_id,artifact_sha256="a"*64,
            reviewer_agent_id="reviewer-1",generation=1,
        ))
        with self.assertRaises(PermissionError):
            self.ledger.submit(
                actor_id="reviewer-1",task_id=self.task.task_id,expected_version=1,
                result=build_review_result(self.task,status="passed",summary="Late result"),
                session_token=self.session,operation_id="late-submit",
                lease_token=self.lease.token,lease_generation=1,
            )

    def test_repeated_reassignment_history_is_replayable(self):
        expired=(datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat()
        with sqlite3.connect(self.db_path) as db:
            db.execute("UPDATE reviewer_leases SET expires_at=? WHERE task_id=?",(expired,self.task.task_id))
        first=self.ledger.reassign(actor_id="supervisor",task_id=self.task.task_id,
            expected_version=0,replacement_reviewer_agent_id="reviewer-2")
        # Simulate the replacement lease expiring, then reassign again.
        expired=(datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat()
        with sqlite3.connect(self.db_path) as db:
            db.execute("UPDATE reviewer_leases SET expires_at=? WHERE task_id=?",(expired,self.task.task_id))
        second=self.ledger.reassign(actor_id="supervisor",task_id=self.task.task_id,
            expected_version=1,replacement_reviewer_agent_id="reviewer-3")
        self.assertEqual(second["version"],2)
        self.assertEqual(second["lease_generation"],3)
        self.assertEqual(first["lease_generation"],2)

    def test_lease_database_must_match_ledger_database(self):
        other = ReviewerLeaseAuthority(
            Path(self.temp.name) / "other.sqlite", secret=b"x" * 32,
            authorize=lambda *_: True,
        )
        with self.assertRaisesRegex(ValueError, "must_share_database"):
            ReviewerTaskLedger(
                Path(self.temp.name) / "different.sqlite",
                authorize=lambda *_: True,
                signer_resolver=lambda *_: self.signer,
                session_authority=self.sessions,
                lease_authority=other,
            )


if __name__ == "__main__":
    unittest.main()
