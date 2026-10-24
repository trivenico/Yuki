"""Tests for static preflight used by trusted plugin discovery."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from core.plugin_loader import _static_candidates


class PluginStaticDiscoveryTests(unittest.TestCase):
    def test_finds_execute_based_skill_without_importing_candidate(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "good_skill.py").write_text(
                "from skills.base import Skill\n"
                "class GoodSkill(Skill):\n"
                "    def execute(self, args): return args\n",
                encoding="utf-8",
            )
            candidates, failures = _static_candidates([root])
            self.assertEqual(candidates, {"good_skill"})
            self.assertEqual(failures, [])

    def test_finds_existing_run_based_skill_api(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "legacy_skill.py").write_text(
                "from skills.base import Skill, SkillResult\n"
                "class LegacySkill(Skill):\n"
                "    name = 'legacy'\n"
                "    def run(self, argument=''): return SkillResult(True, 'ok')\n",
                encoding="utf-8",
            )
            candidates, failures = _static_candidates([root])
            self.assertEqual(candidates, {"legacy_skill"})
            self.assertEqual(failures, [])

    def test_does_not_execute_or_accept_non_skill_module(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            marker = root / "executed.txt"
            (root / "helper.py").write_text(
                f"from pathlib import Path\nPath({str(marker)!r}).write_text('executed')\n",
                encoding="utf-8",
            )
            (root / "no_execute.py").write_text("class FakeSkill:\n    pass\n", encoding="utf-8")
            candidates, failures = _static_candidates([root])
            self.assertEqual(candidates, set())
            self.assertFalse(marker.exists())
            self.assertEqual(failures, [])

    def test_reports_parse_error_and_oversized_source(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "broken.py").write_text("class BrokenSkill(Skill):\n def execute(:\n", encoding="utf-8")
            (root / "large_skill.py").write_text("#" + "x" * (512 * 1024), encoding="utf-8")
            _, failures = _static_candidates([root])
            by_module = {item["module"]: item["error"] for item in failures}
            self.assertEqual(by_module["broken"], "SyntaxError")
            self.assertEqual(by_module["large_skill"], "SourceTooLarge")


if __name__ == "__main__":
    unittest.main()
