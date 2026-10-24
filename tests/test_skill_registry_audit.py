"""Tests for explicit skill-registry wiring audit."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from core.skill_registry_audit import audit_skill_registry


class SkillRegistryAuditTests(unittest.TestCase):
    def test_distinguishes_direct_registry_imports_from_candidates(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            skills = root / "skills"
            skills.mkdir()
            (skills / "registry.py").write_text(
                "from .base import Skill\nfrom .calendar_skill import CalendarSkill\n",
                encoding="utf-8",
            )
            (skills / "base.py").write_text("class Skill: pass\n", encoding="utf-8")
            (skills / "calendar_skill.py").write_text("class CalendarSkill: pass\n", encoding="utf-8")
            (skills / "orphan_skill.py").write_text("class OrphanSkill: pass\n", encoding="utf-8")
            report = audit_skill_registry(root)
            self.assertEqual(report["skill_module_count"], 2)
            self.assertEqual(report["registry_import_count"], 1)
            self.assertEqual(report["directly_wired"], ["calendar_skill"])
            self.assertEqual(report["not_directly_wired"], ["orphan_skill"])

    def test_registry_parse_failure_fails_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            skills = root / "skills"
            skills.mkdir()
            (skills / "registry.py").write_text("def broken(:\n", encoding="utf-8")
            (skills / "some_skill.py").write_text("VALUE = 1\n", encoding="utf-8")
            report = audit_skill_registry(root)
            self.assertEqual(report["registry_import_count"], 0)
            self.assertEqual(report["issues"][0]["path"], "skills/registry.py")
            self.assertIn("no registration conclusions", report["interpretation"])


if __name__ == "__main__":
    unittest.main()
