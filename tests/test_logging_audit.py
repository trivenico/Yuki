import json
import logging
from pathlib import Path
import tempfile
import unittest

from core.approval_audit_jsonl import JsonlApprovalAuditLog
from core.logging_setup import configure_logging, log_event
from core.permissions import PermissionGate


class LoggingSetupTests(unittest.TestCase):
    def test_rotating_utf8_log_is_created_and_bounded(self):
        with tempfile.TemporaryDirectory() as tmp:
            logger = configure_logging(tmp)
            log_event(logger, logging.INFO, "startup", component="runtime")
            for handler in logger.handlers:
                handler.flush()
            log_path = Path(tmp) / "yuki.log"
            self.assertTrue(log_path.exists())
            self.assertIn("event='startup'", log_path.read_text(encoding="utf-8"))
            self.assertEqual(logger.propagate, False)
            # Windows does not allow TemporaryDirectory cleanup while the log
            # handler still owns an open file handle.
            for handler in list(logger.handlers):
                logger.removeHandler(handler)
                handler.close()


class JsonlApprovalAuditTests(unittest.TestCase):
    def test_records_allowlisted_metadata_as_valid_json_lines(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "approval.jsonl"
            audit = JsonlApprovalAuditLog(path)
            audit.record_event(
                "approved", "pending-1", "owner", "session-1",
                risk="high", status="approved",
            )
            rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["event_type"], "approved")
            self.assertEqual(rows[0]["risk"], "high")
            self.assertNotIn("prompt", rows[0])
            self.assertNotIn("arguments", rows[0])

    def test_audit_rejects_unknown_event_and_risk(self):
        with tempfile.TemporaryDirectory() as tmp:
            audit = JsonlApprovalAuditLog(Path(tmp) / "approval.jsonl")
            with self.assertRaises(ValueError):
                audit.record_event("unknown", risk="low")
            with self.assertRaises(ValueError):
                audit.record_event("approved", risk="untrusted")


    def test_permission_gate_writes_metadata_only_audit_events(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "approval.jsonl"
            audit = JsonlApprovalAuditLog(path)
            gate = PermissionGate(approver=lambda request: True, audit_log=audit)
            self.assertTrue(gate.request("notes.write", "approve safe note", "low"))
            action = {"skill_id": "notes.write", "arguments": {"text": "private content"}}
            receipt = gate.issue_receipt(action, lambda shown: True, risk="high")
            self.assertIsNotNone(receipt)
            self.assertTrue(gate.consume_receipt(receipt, action))
            rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
            event_types = [row["event_type"] for row in rows]
            self.assertIn("requested", event_types)
            self.assertIn("approved", event_types)
            serialized = path.read_text(encoding="utf-8")
            self.assertNotIn("private content", serialized)
            self.assertNotIn("approve safe note", serialized)

    def test_audit_failure_fails_closed_for_permission_request(self):
        class BrokenAudit:
            def record_event(self, *args, **kwargs):
                raise OSError("disk unavailable")
        gate = PermissionGate(approver=lambda request: True, audit_log=BrokenAudit())
        self.assertFalse(gate.request("notes.write", "reason", "low"))

    def test_rotation_keeps_bounded_number_of_backups(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "approval.jsonl"
            audit = JsonlApprovalAuditLog(path, max_bytes=1024, backup_count=2)
            for index in range(30):
                audit.record_event("approved", "p" * 80 + str(index), "owner", "session", risk="high")
            self.assertLessEqual(path.stat().st_size, 1024)
            self.assertTrue(path.with_name("approval.jsonl.1").exists())
            self.assertTrue(path.with_name("approval.jsonl.2").exists())
            self.assertFalse(path.with_name("approval.jsonl.3").exists())


if __name__ == "__main__":
    unittest.main()
