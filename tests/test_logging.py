import hashlib
import json
import logging
import tempfile
import unittest
from logging.handlers import RotatingFileHandler
from pathlib import Path

from core.logging_config import setup_logger, log_approval_audit
from core.permissions import PermissionGate


class LoggingTests(unittest.TestCase):
    def test_setup_logger_creates_log_directory_and_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            logger = setup_logger(workspace)
            log_file = workspace / "logs" / "yuki.log"
            try:
                logger.info("Test log line")
                self.assertTrue(log_file.exists())
                content = log_file.read_text(encoding="utf-8")
                self.assertIn("Test log line", content)
            finally:
                for handler in list(logger.handlers):
                    if isinstance(handler, RotatingFileHandler) and handler.baseFilename == str(log_file.resolve()):
                        logger.removeHandler(handler)
                        handler.close()

    def test_log_approval_audit_writes_jsonl(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            gate = PermissionGate(approver=lambda req: True, workspace=workspace)
            decision = gate.request("file_write", "Save notes", "medium")
            self.assertTrue(decision)

            audit_file = workspace / "approval_audit.jsonl"
            self.assertTrue(audit_file.exists())
            lines = audit_file.read_text(encoding="utf-8").strip().splitlines()
            self.assertEqual(len(lines), 1)
            record = json.loads(lines[0])
            self.assertEqual(record["action_fingerprint"], hashlib.sha256(b"file_write").hexdigest())
            self.assertNotIn("action", record)
            self.assertNotIn("reason", record)
            self.assertNotIn("Save notes", lines[0])
            self.assertEqual(record["decision"], "approved")


if __name__ == "__main__":
    unittest.main()
