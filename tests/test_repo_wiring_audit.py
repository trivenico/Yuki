"""Tests for the static Python repository wiring audit."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from core.repo_wiring_audit import audit_python_wiring


class RepositoryWiringAuditTests(unittest.TestCase):
    def test_marks_imported_and_unreferenced_modules_separately(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "main.py").write_text("import used_module\n", encoding="utf-8")
            (root / "used_module.py").write_text("VALUE = 1\n", encoding="utf-8")
            (root / "orphan_candidate.py").write_text("VALUE = 2\n", encoding="utf-8")
            report = audit_python_wiring(root)
            by_path = {item["path"]: item for item in report["files"]}
            self.assertEqual(report["python_file_count"], 3)
            self.assertEqual(by_path["used_module.py"]["status"], "statically_imported")
            self.assertEqual(by_path["orphan_candidate.py"]["status"], "no_static_inbound_import")
            self.assertEqual(by_path["main.py"]["status"], "possible_entrypoint")

    def test_excludes_generated_directories_and_reports_syntax_errors(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / ".venv").mkdir()
            (root / ".venv" / "ignored.py").write_text("pass\n", encoding="utf-8")
            (root / "broken.py").write_text("def bad(:\n", encoding="utf-8")
            report = audit_python_wiring(root)
            self.assertEqual(report["python_file_count"], 1)
            self.assertEqual(report["parse_or_size_issues"][0]["reason"], "SyntaxError")
            self.assertEqual(report["files"][0]["status"], "needs_inspection")


if __name__ == "__main__":
    unittest.main()
