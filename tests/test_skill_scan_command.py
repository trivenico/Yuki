"""Tests for the terminal skill scan command."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yuki
from core.plugin_loader import _static_candidates


class SkillScanCommandTests(unittest.TestCase):
    def test_scan_is_read_only_and_lists_candidates(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "sample_skill.py").write_text(
                "from skills.base import Skill\n"
                "class SampleSkill(Skill):\n"
                "    def execute(self, args): return args\n",
                encoding="utf-8",
            )
            with patch.object(yuki.Path, "with_name", return_value=root), \
                 patch.object(yuki, "write_audit_event") as audit:
                result = yuki.handle_command("/skills scan", [], [], None, None)
            self.assertIn("sample_skill", result)
            self.assertIn("does not trust, import, register, or execute", result)
            audit.assert_called_once()

    def test_scan_usage_is_clear(self):
        result = yuki.handle_command("/skills", [], [], None, None)
        self.assertEqual(result, "Usage: /skills scan")


if __name__ == "__main__":
    unittest.main()
