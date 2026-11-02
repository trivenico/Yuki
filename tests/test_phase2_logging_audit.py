import logging
import tempfile
import unittest
from pathlib import Path

from core.logging_setup import configure_logging, log_event
from core.approval_audit import SQLiteApprovalAuditLog
from core.permissions import PermissionGate


class PhaseTwoLoggingAuditTests(unittest.TestCase):
    def tearDown(self):
        logger = logging.getLogger("yuki")
        for handler in list(logger.handlers):
            logger.removeHandler(handler)
            handler.close()

    def test_rotating_logger_writes_utf8_and_sanitizes_line_breaks(self):
        with tempfile.TemporaryDirectory() as tmp:
            logger = configure_logging(tmp)
            log_event(logger, logging.INFO, "started\nforged", component="runtime")
            for handler in logger.handlers:
                handler.flush()
            text = (Path(tmp) / "yuki.log").read_text(encoding="utf-8")
            self.assertIn("event='started forged'", text)
            self.assertEqual(len(text.splitlines()), 1)
            self.assertFalse(logger.propagate)

    def test_logger_configuration_is_idempotent_for_same_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            first = configure_logging(tmp)
            count = len(first.handlers)
            second = configure_logging(tmp)
            self.assertIs(first, second)
            self.assertEqual(len(second.handlers), count)

    def test_permission_gate_audits_only_fingerprint(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "audit.sqlite3")
            audit = SQLiteApprovalAuditLog(path)
            gate = PermissionGate(lambda request: True, audit_log=audit)
            self.assertTrue(gate.request("skill:notes.write:secret-args", "private prompt text", "high"))
            events = audit.list_events()
            self.assertEqual([row["event_type"] for row in reversed(events)], ["requested", "approved"])
            with open(path, "rb") as stream:
                data = stream.read()
            self.assertNotIn(b"secret-args", data)
            self.assertNotIn(b"private prompt text", data)
            self.assertTrue(audit.verify_chain())

    def test_audit_failure_prevents_approval(self):
        class BrokenAudit:
            def append(self, **kwargs):
                raise OSError("storage unavailable")
        called = []
        gate = PermissionGate(lambda request: called.append(True) or True, audit_log=BrokenAudit())
        self.assertFalse(gate.request("safe-action", "reason", "low"))
        self.assertEqual(called, [])


if __name__ == "__main__":
    unittest.main()
