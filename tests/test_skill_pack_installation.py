"""Regression tests for safe data-only skill packs."""
import json
import tempfile
import unittest
from pathlib import Path

from core.skill_packs import (
    InstalledSkillPack,
    install_skill_pack,
    load_installed_skill_packs,
    read_skill_pack,
    remove_skill_pack,
    validate_skill_pack,
)


class SkillPackTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def manifest(self, **overrides):
        value = {
            "schema_version": 1,
            "name": "taste_helper",
            "description": "A bounded writing helper",
            "instructions": "Help improve clarity without taking external actions.",
            "risk": "low",
        }
        value.update(overrides)
        return value

    def test_install_load_and_remove_round_trip(self):
        source = self.root / "pack.json"
        source.write_text(json.dumps(self.manifest()), encoding="utf-8")
        target = install_skill_pack(source, self.root / "installed")
        self.assertEqual(target.name, "taste_helper.json")

        loaded, errors = load_installed_skill_packs(self.root / "installed")
        self.assertEqual(errors, [])
        self.assertEqual([item.name for item in loaded], ["taste_helper"])
        result = loaded[0].execute("Improve this paragraph")
        self.assertTrue(result.ok)
        self.assertIn("untrusted task instructions", result.message)
        self.assertFalse(remove_skill_pack("taste_helper", self.root / "installed"))
        self.assertTrue(remove_skill_pack("taste_helper", self.root / "installed"))

    def test_rejects_unknown_fields_and_duplicate_json_keys(self):
        with self.assertRaises(ValueError):
            validate_skill_pack(self.manifest(extra="not allowed"))
        source = self.root / "duplicate.json"
        source.write_text(
            '{"schema_version":1,"schema_version":1,"name":"taste_helper",'
            '"description":"x","instructions":"y","risk":"low"}',
            encoding="utf-8",
        )
        with self.assertRaises(ValueError):
            read_skill_pack(source)

    def test_rejects_symlink_source_and_oversized_source(self):
        source = self.root / "pack.json"
        source.write_text(json.dumps(self.manifest()), encoding="utf-8")
        link = self.root / "link.json"
        try:
            link.symlink_to(source)
        except (OSError, NotImplementedError):
            self.skipTest("Symlinks are unavailable on this platform.")
        with self.assertRaises(ValueError):
            read_skill_pack(link)

        large = self.root / "large.json"
        large.write_text(" " * (64 * 1024 + 1), encoding="utf-8")
        with self.assertRaises(ValueError):
            read_skill_pack(large)

    def test_non_low_risk_pack_requires_approval(self):
        skill = InstalledSkillPack(self.manifest(risk="high"))
        self.assertTrue(skill.requires_approval)
        self.assertEqual(skill.risk, "high")


if __name__ == "__main__":
    unittest.main()
