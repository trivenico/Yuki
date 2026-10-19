import json
import tempfile
import unittest
from pathlib import Path

from core.logging_config import setup_logger, log_approval_audit
from core.permissions import PermissionGate


class LoggingTests(unittest.TestCase):
    def test_setup_logger_creates_log_directory_and_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            logger = setup_logger(workspace)
            logger.info("Test log line")
            log_file = workspace / "logs" / "yuki.log"
            self.assertTrue(log_file.exists())
            content = log_file.read_text(encoding="utf-8")
            self.assertIn("Test log line", content)

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
            self.assertEqual(record["action"], "file_write")
            self.assertEqual(record["decision"], "approved")


if __name__ == "__main__":
    unittest.main()
