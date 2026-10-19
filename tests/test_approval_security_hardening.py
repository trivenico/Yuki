import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from core.action_approvals import ActionApprovalStore
from core.logging_config import log_approval_audit
from core.permissions import PermissionGate


class ApprovalSecurityHardeningTests(unittest.TestCase):
    def test_audit_record_excludes_raw_action_and_reason(self):
        with tempfile.TemporaryDirectory() as tmp:
            action = "secret-prompt-value and token=do-not-log"
            reason = "private rationale must not be persisted"
            log_approval_audit(Path(tmp), action, reason, "high", False)
            record = json.loads((Path(tmp) / "approval_audit.jsonl").read_text(encoding="utf-8").strip())
            serialized = json.dumps(record)
            self.assertNotIn(action, serialized)
            self.assertNotIn(reason, serialized)
            self.assertIn("action_fingerprint", record)
            self.assertEqual(record["decision"], "denied")

    def test_audit_write_failure_denies_approval(self):
        with tempfile.TemporaryDirectory() as tmp:
            gate = PermissionGate(
                approver=lambda request: True,
                workspace=Path(tmp),
                timeout_seconds=0.2,
            )
            with patch("core.permissions.log_approval_audit", side_effect=OSError("disk full")):
                self.assertFalse(gate.request("sensitive action", "test reason", "high"))

    def test_approver_timeout_denies_and_does_not_later_approve(self):
        finished = threading.Event()

        def slow_approver(request):
            time.sleep(0.15)
            finished.set()
            return True

        gate = PermissionGate(approver=slow_approver, timeout_seconds=0.02)
        start = time.monotonic()
        self.assertFalse(gate.request("action", "reason", "medium"))
        self.assertLess(time.monotonic() - start, 0.12)
        self.assertTrue(finished.wait(0.5))
        # A timed-out callback's eventual True result is never returned as approval.
        self.assertFalse(gate.request("action", "reason", "medium") is True)

    def test_rate_limit_counts_denials_not_only_approvals(self):
        calls = []
        gate = PermissionGate(
            approver=lambda request: calls.append(request) or False,
            max_rate_limit=2,
            rate_window_seconds=60,
            timeout_seconds=0.2,
        )
        self.assertFalse(gate.request("action", "reason"))
        self.assertFalse(gate.request("action", "reason"))
        self.assertFalse(gate.request("action", "reason"))
        self.assertEqual(len(calls), 2)

    def test_missing_approver_fails_closed(self):
        self.assertFalse(PermissionGate().request("action", "reason"))

    def test_legacy_action_approval_store_cannot_grant_authorization(self):
        store = ActionApprovalStore()
        self.assertFalse(store.is_approved("arbitrary-action-id"))
        with self.assertRaises(RuntimeError):
            store.approve("arbitrary-action-id", True)
        self.assertFalse(store.is_approved("arbitrary-action-id"))


if __name__ == "__main__":
    unittest.main()
