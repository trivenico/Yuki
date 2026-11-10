"""Tests for the conservative AST wiring audit."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts.audit_python_wiring import audit


class WiringAuditTests(unittest.TestCase):
    def test_follows_normal_and_relative_imports_from_roots(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "main.py").write_text("from core.agent import Agent\n", encoding="utf-8")
            (root / "core").mkdir()
            (root / "core" / "__init__.py").write_text("", encoding="utf-8")
            (root / "core" / "agent.py").write_text("from .helpers import work\n", encoding="utf-8")
            (root / "core" / "helpers.py").write_text("def work(): pass\n", encoding="utf-8")
            (root / "unused.py").write_text("def unused(): pass\n", encoding="utf-8")
            result = audit(root, entrypoints=("main.py",))
            by_path = {item["path"]: item["classification"] for item in result["modules"]}
            self.assertEqual(by_path["main.py"], "statically reachable")
            self.assertEqual(by_path["core/agent.py"], "statically reachable")
            self.assertEqual(by_path["core/helpers.py"], "statically reachable")
            self.assertEqual(by_path["unused.py"], "not reached from configured static roots")

    def test_reports_parse_errors_without_crashing(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "main.py").write_text("def broken(:\n", encoding="utf-8")
            result = audit(root, entrypoints=("main.py",))
            self.assertEqual(len(result["parse_errors"]), 1)


if __name__ == "__main__":
    unittest.main()
