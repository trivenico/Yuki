import tempfile
import sqlite3
import unittest
from pathlib import Path

from automation.reviewer_contracts import ReviewFinding, build_review_result
from automation.reviewer_team import ReviewerAssignment, ReviewerTeamPlan
from automation.reviewer_workflow import build_review_tasks
from automation.reviewer_signing import HMACReviewerSigner
from automation.reviewer_ledger import (
    ReviewerLedgerConflict, ReviewerTaskLedger,
)
from automation.reviewer_sessions import ReviewerSessionAuthority


def tasks():
    plan = ReviewerTeamPlan(
        artifact_sha256="a" * 64,
        author_agent_id="builder",
        assignments=(ReviewerAssignment("security", "security-reviewer"),),
    )
    return build_review_tasks(
        plan,
        requirements_by_check={"security": ("Inspect authorization checks",)},
        evidence_references_by_check={"security": ("snapshot://artifact/source",)},
    )


class ReviewerTaskLedgerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.temp.name) / "review-ledger.sqlite")
        self.allowed = True
        self.signer = HMACReviewerSigner(key_id="review-key-v1", key=b"z" * 32)
        self.sessions = ReviewerSessionAuthority(
            Path(self.temp.name) / "sessions.sqlite", secret=b"s" * 32,
            authorize=lambda actor, action, digest: (
                self.allowed and actor == "security-reviewer" and digest == "a" * 64
            ),
        )
        self.session_token = self.sessions.issue(
            reviewer_agent_id="security-reviewer", artifact_sha256="a" * 64
        )
        self.ledger = ReviewerTaskLedger(
            self.path,
            authorize=lambda actor, action, digest: self.allowed and digest == "a" * 64,
            signer_resolver=lambda actor, key_id: self.signer if key_id in (None, "review-key-v1") else None,
            session_authority=self.sessions,
        )
        self.task = tasks()[0]

    def tearDown(self):
        self.temp.cleanup()

    def test_create_is_idempotent_and_records_assignment(self):
        first = self.ledger.create_tasks(actor_id="supervisor", tasks=(self.task,))
        second = self.ledger.create_tasks(actor_id="supervisor", tasks=(self.task,))
        self.assertFalse(first[0]["reused"])
        self.assertTrue(second[0]["reused"])
        record = self.ledger.get(actor_id="auditor", task_id=self.task.task_id)
        self.assertEqual(record["state"], "assigned")
        self.assertEqual(record["events"][0]["type"], "assigned")

    def test_claim_requires_assigned_reviewer_and_version(self):
        self.ledger.create_tasks(actor_id="supervisor", tasks=(self.task,))
        with self.assertRaises(PermissionError):
            self.ledger.claim(actor_id="other-reviewer", task_id=self.task.task_id, expected_version=0,
                              session_token=self.session_token, operation_id="wrong-reviewer")
        claimed = self.ledger.claim(
            actor_id="security-reviewer", task_id=self.task.task_id, expected_version=0,
            session_token=self.session_token, operation_id="claim-task-1",
        )
        self.assertEqual(claimed["state"], "in_progress")
        with self.assertRaises(ReviewerLedgerConflict):
            self.ledger.claim(
                actor_id="security-reviewer", task_id=self.task.task_id, expected_version=0,
                session_token=self.session_token, operation_id="claim-task-retry",
            )

    def test_submit_binds_result_and_is_idempotent(self):
        self.ledger.create_tasks(actor_id="supervisor", tasks=(self.task,))
        self.ledger.claim(actor_id="security-reviewer", task_id=self.task.task_id, expected_version=0,
                           session_token=self.session_token, operation_id="claim-" + str(self.id()) if hasattr(self, "id") else "claim-task")
        result = build_review_result(
            self.task, status="failed", summary="A security issue needs remediation.",
            findings=(ReviewFinding(
                finding_id="authz-issue", severity="high", state="open",
                title="Missing authorization check",
                detail="The sensitive operation does not enforce the required permission.",
                evidence_reference="snapshot://artifact/source",
                recommendation="Enforce the permission before executing the operation.",
            ),),
        )
        first = self.ledger.submit(
            actor_id="security-reviewer", task_id=self.task.task_id,
            expected_version=1, result=result, session_token=self.session_token,
            operation_id="submit-task-1",
        )
        second = self.ledger.submit(
            actor_id="security-reviewer", task_id=self.task.task_id,
            expected_version=1, result=result, session_token=self.session_token,
            operation_id="submit-task-1-retry",
        )
        self.assertFalse(first["reused"])
        self.assertTrue(second["reused"])
        record = self.ledger.get(actor_id="auditor", task_id=self.task.task_id)
        self.assertEqual(record["state"], "submitted")
        self.assertEqual(record["result"]["findings"][0]["severity"], "high")
        self.assertTrue(record["signature_verified"])
        self.assertEqual(record["signature"]["key_id"], "review-key-v1")
        self.assertEqual(record["events"][-1]["type"], "submitted")

    def test_conflicting_second_submission_is_rejected(self):
        self.ledger.create_tasks(actor_id="supervisor", tasks=(self.task,))
        self.ledger.claim(actor_id="security-reviewer", task_id=self.task.task_id, expected_version=0,
                           session_token=self.session_token, operation_id="claim-" + str(self.id()) if hasattr(self, "id") else "claim-task")
        result = build_review_result(self.task, status="passed", summary="No findings.")
        self.ledger.submit(
            actor_id="security-reviewer", task_id=self.task.task_id,
            expected_version=1, result=result, session_token=self.session_token,
            operation_id="submit-conflict-first",
        )
        different = build_review_result(self.task, status="failed", summary="Different result.")
        with self.assertRaises(ReviewerLedgerConflict):
            self.ledger.submit(
                actor_id="security-reviewer", task_id=self.task.task_id,
                expected_version=1, result=different, session_token=self.session_token,
                operation_id="submit-task-conflict",
            )

    def test_read_rejects_invalid_signature(self):
        self.ledger.create_tasks(actor_id="supervisor", tasks=(self.task,))
        self.ledger.claim(actor_id="security-reviewer", task_id=self.task.task_id, expected_version=0,
                           session_token=self.session_token, operation_id="claim-" + str(self.id()) if hasattr(self, "id") else "claim-task")
        result = build_review_result(self.task, status="passed", summary="No findings.")
        self.ledger.submit(actor_id="security-reviewer", task_id=self.task.task_id,
                           expected_version=1, result=result, session_token=self.session_token,
                           operation_id="submit-task-signature")
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE reviewer_tasks SET signature_json=? WHERE task_id=?",
                       ('{"key_id":"review-key-v1","algorithm":"hmac-sha256","signature_hex":"' + "0" * 64 + '"}', self.task.task_id))
        from automation.reviewer_ledger import ReviewerLedgerError
        with self.assertRaisesRegex(ReviewerLedgerError, "signature_invalid"):
            self.ledger.get(actor_id="auditor", task_id=self.task.task_id)

    def test_read_detects_tampered_task_payload(self):
        self.ledger.create_tasks(actor_id="supervisor", tasks=(self.task,))
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE reviewer_tasks SET task_json=? WHERE task_id=?",
                       ('{"task_id":"forged"}', self.task.task_id))
        from automation.reviewer_ledger import ReviewerLedgerError
        with self.assertRaisesRegex(ReviewerLedgerError, "task_integrity_mismatch"):
            self.ledger.get(actor_id="auditor", task_id=self.task.task_id)

    def test_read_detects_tampered_event_history(self):
        self.ledger.create_tasks(actor_id="supervisor", tasks=(self.task,))
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE reviewer_task_events SET payload_json=? WHERE task_id=?",
                       ('{}', self.task.task_id))
        from automation.reviewer_ledger import ReviewerLedgerError
        with self.assertRaisesRegex(ReviewerLedgerError, "assignment_event_mismatch"):
            self.ledger.get(actor_id="auditor", task_id=self.task.task_id)

    def test_authorization_fails_closed(self):
        self.allowed = False
        with self.assertRaises(PermissionError):
            self.ledger.create_tasks(actor_id="supervisor", tasks=(self.task,))

    def test_result_cannot_be_submitted_before_claim(self):
        self.ledger.create_tasks(actor_id="supervisor", tasks=(self.task,))
        result = build_review_result(self.task, status="blocked", summary="Cannot start.")
        with self.assertRaises(ReviewerLedgerConflict):
            self.ledger.submit(
                actor_id="security-reviewer", task_id=self.task.task_id,
                expected_version=0, result=result, session_token=self.session_token,
                operation_id="submit-before-claim",
            )


if __name__ == "__main__":
    unittest.main()
