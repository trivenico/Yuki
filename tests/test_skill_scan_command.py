"""Tests for the terminal skill scan command."""
from __future__ import annotations

import unittest
from unittest.mock import patch

import yuki


class SkillScanCommandTests(unittest.TestCase):
    def test_scan_reports_candidates_without_registration(self):
        with patch.object(yuki, "_static_candidates", return_value=({"sample_skill"}, [])) as scan, \
             patch.object(yuki, "write_audit_event") as audit:
            result = yuki.handle_command("/skills scan", [], [], None, None)
        scan.assert_called_once()
        self.assertIn("sample_skill", result)
        self.assertIn("does not trust, import, register, or execute", result)
        audit.assert_called_once()

    def test_scan_reports_parse_issues(self):
        with patch.object(yuki, "_static_candidates", return_value=(set(), [{"module": "broken", "error": "SyntaxError"}])):
            with patch.object(yuki, "write_audit_event"):
                result = yuki.handle_command("/skills scan", [], [], None, None)
        self.assertIn("broken", result)
        self.assertIn("SyntaxError", result)

    def test_scan_usage_is_clear(self):
        result = yuki.handle_command("/skills", [], [], None, None)
        self.assertEqual(result, "Usage: /skills scan")


if __name__ == "__main__":
    unittest.main()
