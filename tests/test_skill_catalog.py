"""Tests for static skill catalog discovery."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from skills.skill_catalog import discover_skill_candidates


class SkillCatalogTests(unittest.TestCase):
    def test_discovers_skill_subclass_without_importing_module(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "sample_skill.py").write_text(
                "from skills.base import Skill\n"
                "class SampleSkill(Skill):\n"
                "    name = 'sample'\n"
                "    def execute(self, args): return args\n",
                encoding="utf-8",
            )
            result = discover_skill_candidates(root)
            self.assertEqual([candidate.module for candidate in result.candidates], ["sample_skill"])
            self.assertEqual(result.candidates[0].class_names, ("SampleSkill",))
            self.assertTrue(result.candidates[0].has_execute_method)

    def test_skips_non_skill_modules_and_private_files(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "helper.py").write_text("def work(): pass\n", encoding="utf-8")
            (root / "_private.py").write_text("raise RuntimeError('must not execute')\n", encoding="utf-8")
            result = discover_skill_candidates(root)
            self.assertEqual(result.candidates, ())
            skipped = {item["module"] for item in result.skipped}
            self.assertIn("helper", skipped)
            self.assertIn("_private", skipped)

    def test_reports_syntax_errors_without_importing(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "broken.py").write_text("class BrokenSkill(Skill):\n def execute(:\n", encoding="utf-8")
            result = discover_skill_candidates(root)
            self.assertEqual(result.candidates, ())
            self.assertEqual(result.failed[0]["module"], "broken")
            self.assertEqual(result.failed[0]["error"], "SyntaxError")

    def test_source_size_is_bounded(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "huge_skill.py").write_text("#" + ("x" * (512 * 1024)), encoding="utf-8")
            result = discover_skill_candidates(root)
            self.assertEqual(result.candidates, ())
            self.assertIn("size limit", result.skipped[0]["reason"])


if __name__ == "__main__":
    unittest.main()
