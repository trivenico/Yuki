import tempfile
import unittest
from pathlib import Path

from core.approval_audit import SQLiteApprovalAuditLog
from training.learning_authorization import LearningAuthorizationError, LearningAuthorizationLedger


class LearningAuthorizationLedgerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.audit = SQLiteApprovalAuditLog(str(Path(self.temp.name) / "learning-audit.sqlite3"))
        self.ledger = LearningAuthorizationLedger(self.audit, actor_id="authenticated-owner")

    def tearDown(self):
        self.temp.cleanup()

    def test_learning_and_training_consent_are_separate_and_durable(self):
        self.ledger.record_consent("learning", True)
        self.assertTrue(self.ledger.has_consent("learning"))
        self.assertFalse(self.ledger.has_consent("training"))
        reopened = LearningAuthorizationLedger(self.audit, actor_id="authenticated-owner")
        self.assertTrue(reopened.has_consent("learning"))
        self.assertFalse(reopened.has_consent("training"))
        reopened.record_consent("training", True)
        self.assertTrue(reopened.has_consent("training"))

    def test_consent_revocation_is_durable_and_purpose_scoped(self):
        self.ledger.record_consent("learning", True)
        self.ledger.record_consent("training", True)
        self.ledger.record_consent("training", False)
        self.assertTrue(self.ledger.has_consent("learning"))
        self.assertFalse(self.ledger.has_consent("training"))

    def test_example_approval_is_bound_to_exact_actor_and_proposal(self):
        proposal = "a" * 24
        self.ledger.record_example_approval(proposal, True)
        self.assertTrue(self.ledger.verify_example_approval(proposal, "authenticated-owner"))
        self.assertFalse(self.ledger.verify_example_approval(proposal, "other-user"))
        self.assertFalse(self.ledger.verify_example_approval("b" * 24, "authenticated-owner"))
        self.ledger.record_example_approval(proposal, False)
        self.assertFalse(self.ledger.verify_example_approval(proposal, "authenticated-owner"))

    def test_audit_tampering_fails_closed(self):
        self.ledger.record_consent("learning", True)
        import sqlite3
        db = sqlite3.connect(self.audit.database_path)
        try:
            db.execute("UPDATE approval_audit_events SET skill_id='tampered' WHERE event_id=1")
            db.commit()
        finally:
            db.close()
        self.assertFalse(self.ledger.has_consent("learning"))

    def test_invalid_consent_and_proposal_values_are_rejected(self):
        with self.assertRaises(ValueError):
            self.ledger.record_consent("voice-cloning", True)
        with self.assertRaises(ValueError):
            self.ledger.record_consent("learning", 1)
        with self.assertRaises(ValueError):
            self.ledger.record_example_approval("not-a-proposal", True)

    def test_audit_write_failure_does_not_claim_consent(self):
        class BrokenAudit:
            def append(self, **kwargs):
                raise OSError("disk unavailable")
        broken = LearningAuthorizationLedger(BrokenAudit(), actor_id="owner")
        with self.assertRaises(LearningAuthorizationError):
            broken.record_consent("learning", True)


if __name__ == "__main__":
    unittest.main()
