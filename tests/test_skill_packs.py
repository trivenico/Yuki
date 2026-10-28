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
        self.source = self.root / "source.json"
        self.directory = self.root / "installed"

    def tearDown(self):
        self.temp.cleanup()

    def pack(self, **overrides):
        value = {
            "schema_version": 1,
            "name": "release_notes",
            "description": "Draft accurate release notes.",
            "instructions": "Summarize verified changes and do not invent claims.",
            "risk": "low",
        }
        value.update(overrides)
        return value

    def write_pack(self, value):
        self.source.write_text(json.dumps(value), encoding="utf-8")
        return self.source

    def test_validate_normalizes_text_and_enforces_exact_schema(self):
        checked = validate_skill_pack(self.pack(description="  Write release notes.  "))
        self.assertEqual(checked["description"], "Write release notes.")
        with self.assertRaisesRegex(ValueError, "exactly"):
            validate_skill_pack(self.pack(extra="not allowed"))

    def test_read_rejects_duplicate_json_keys(self):
        self.source.write_text(
            '{"schema_version":1,"name":"first_name","name":"second_name",'
            '"description":"desc","instructions":"instructions","risk":"low"}',
            encoding="utf-8",
        )
        with self.assertRaisesRegex(ValueError, "valid UTF-8 JSON"):
            read_skill_pack(self.source)

    def test_install_load_execute_and_remove_data_only_pack(self):
        install_skill_pack(self.write_pack(self.pack()), self.directory)
        loaded, errors = load_installed_skill_packs(self.directory)
        self.assertEqual(errors, [])
        self.assertEqual(len(loaded), 1)
        self.assertIsInstance(loaded[0], InstalledSkillPack)
        self.assertFalse(loaded[0].requires_approval)
        result = loaded[0].execute("prepare v1.2 notes")
        self.assertTrue(result.ok)
        self.assertIn("do not invent claims", result.message)
        self.assertIn("prepare v1.2 notes", result.message)
        self.assertTrue(remove_skill_pack("release_notes", self.directory))
        self.assertEqual(load_installed_skill_packs(self.directory), ([], []))

    def test_medium_and_high_risk_packs_require_approval(self):
        for risk in ("medium", "high"):
            with self.subTest(risk=risk):
                self.assertTrue(InstalledSkillPack(self.pack(risk=risk)).requires_approval)

    def test_install_never_overwrites_existing_pack(self):
        self.directory.mkdir()
        target = self.directory / "release_notes.json"
        target.write_text('{"original":true}', encoding="utf-8")
        with self.assertRaises(FileExistsError):
            install_skill_pack(self.write_pack(self.pack()), self.directory)
        self.assertEqual(target.read_text(encoding="utf-8"), '{"original":true}')

    def test_loader_reports_invalid_installed_pack_without_loading_it(self):
        self.directory.mkdir()
        (self.directory / "broken.json").write_text('{"risk":"low"}', encoding="utf-8")
        loaded, errors = load_installed_skill_packs(self.directory)
        self.assertEqual(loaded, [])
        self.assertEqual(errors[0]["file"], "broken.json")

    def test_remove_validates_pack_name(self):
        with self.assertRaisesRegex(ValueError, "Invalid"):
            remove_skill_pack("../outside", self.directory)


if __name__ == "__main__":
    unittest.main()
