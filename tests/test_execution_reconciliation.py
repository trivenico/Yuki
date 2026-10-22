"""Tests for audited operator reconciliation without automatic replay."""
import unittest
import tempfile
from pathlib import Path

from core.approval_audit import SQLiteApprovalAuditLog
from core.execution_ledger import SQLiteExecutionLedger
from core.execution_reconciliation import SQLiteExecutionReconciliation


class ExecutionReconciliationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.ledger = SQLiteExecutionLedger(str(root / "ledger.sqlite3"))
        self.audit = SQLiteApprovalAuditLog(str(root / "audit.sqlite3"))
        self.reconciliation = SQLiteExecutionReconciliation(
            str(root / "reconciliation.sqlite3"), ledger=self.ledger, audit_log=self.audit
        )
        self.key = "approval-uncertain-1"
        self.action = {"skill_id": "knowledge:index", "arguments": {"relative_path": "notes.md"}}
        self.ledger.begin(self.key, self.action)
        self.ledger.transition(self.key, "running")

    def tearDown(self):
        self.temp.cleanup()

    def test_pending_action_can_be_resolved_with_evidence_and_is_not_replayed(self):
        pending = self.reconciliation.list_pending()
        self.assertEqual([item.idempotency_key for item in pending], [self.key])
        result = self.reconciliation.resolve(
            self.key, operator="operator-1", disposition="confirmed_completed",
            evidence="Provider receipt 82 confirms the index write completed.",
        )
        self.assertFalse(result["replay_permitted"])
        self.assertEqual(self.ledger.get(self.key).state, "running")
        self.assertEqual(self.reconciliation.list_pending(), [])
        self.assertTrue(self.audit.verify_chain())

    def test_resolution_requires_specific_evidence(self):
        with self.assertRaisesRegex(ValueError, "Evidence"):
            self.reconciliation.resolve(
                self.key, operator="operator-1", disposition="unresolved", evidence="unknown"
            )
        self.assertEqual(self.ledger.get(self.key).state, "running")

    def test_duplicate_resolution_is_rejected(self):
        args = dict(operator="operator-1", disposition="unresolved",
                    evidence="No provider evidence is available; manual investigation required.")
        self.reconciliation.resolve(self.key, **args)
        with self.assertRaisesRegex(ValueError, "already has"):
            self.reconciliation.resolve(self.key, **args)

    def test_invalid_disposition_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "disposition"):
            self.reconciliation.resolve(
                self.key, operator="operator-1", disposition="retry_now",
                evidence="Operator wants an immediate retry without evidence.",
            )

    def test_missing_execution_cannot_be_resolved(self):
        with self.assertRaisesRegex(ValueError, "not awaiting"):
            self.reconciliation.resolve(
                "missing", operator="operator-1", disposition="unresolved",
                evidence="No execution record found in durable execution ledger.",
            )


if __name__ == "__main__":
    unittest.main()
