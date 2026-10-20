"""Phase 51 production approval-composition guard tests."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.approval_audit import SQLiteApprovalAuditLog
from core.approval_flow import ApprovalCoordinator
from core.pending_approvals import SQLitePendingApprovalStore


class Phase51ProductionApprovalCompositionTests(unittest.TestCase):
    def test_production_rejects_missing_pending_store(self):
        with patch.dict(os.environ, {"YUKI_ENV": "production"}):
            with self.assertRaisesRegex(RuntimeError, "durable pending storage"):
                ApprovalCoordinator(audit_log=object())

    def test_production_rejects_missing_audit_log(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"YUKI_ENV": "production"}):
            pending = SQLitePendingApprovalStore(str(Path(directory) / "pending.sqlite3"))
            with self.assertRaisesRegex(RuntimeError, "durable pending storage"):
                ApprovalCoordinator(pending_store=pending)

    def test_production_accepts_both_durable_dependencies(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"YUKI_ENV": "production"}):
            pending = SQLitePendingApprovalStore(str(Path(directory) / "pending.sqlite3"))
            audit = SQLiteApprovalAuditLog(str(Path(directory) / "audit.sqlite3"))
            coordinator = ApprovalCoordinator(pending_store=pending, audit_log=audit)
            self.assertIs(coordinator.pending_store, pending)
            self.assertIs(coordinator.audit_log, audit)

    def test_development_keeps_test_friendly_memory_defaults(self):
        with patch.dict(os.environ, {"YUKI_ENV": "development"}):
            coordinator = ApprovalCoordinator()
            self.assertIsNone(coordinator.pending_store)
            self.assertIsNone(coordinator.audit_log)


if __name__ == "__main__":
    unittest.main()
