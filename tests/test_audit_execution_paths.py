"""Tests for the static execution-path inventory tool."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts.audit_execution_paths import scan_python


class ExecutionPathAuditTests(unittest.TestCase):
    def test_finds_tool_dispatch_and_dynamic_execution_candidates(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "sample.py").write_text(
                "def f(obj, name):\n"
                "    run_tool(name)\n"
                "    obj.execute()\n"
                "    importlib.import_module(name)\n"
                "    eval('1 + 1')\n",
                encoding="utf-8",
            )
            found = scan_python(root)
            self.assertEqual(
                [(item.callee, item.category) for item in found],
                [
                    ("run_tool", "direct_tool_dispatch"),
                    ("obj.execute", "capability_execution_candidate"),
                    ("importlib.import_module", "dynamic_execution_or_import"),
                    ("eval", "dynamic_execution_or_import"),
                ],
            )

    def test_skips_virtualenv_and_malformed_python(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / ".venv").mkdir()
            (root / ".venv" / "ignored.py").write_text("eval('x')", encoding="utf-8")
            (root / "bad.py").write_text("def invalid(:", encoding="utf-8")
            (root / "ok.py").write_text("run_tool('time')", encoding="utf-8")
            found = scan_python(root)
            self.assertEqual(len(found), 1)
            self.assertEqual(found[0].path, "ok.py")


if __name__ == "__main__":
    unittest.main()
