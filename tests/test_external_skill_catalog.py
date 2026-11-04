import json
import tempfile
import unittest
from pathlib import Path

from core.external_skill_catalog import ExternalSkillCatalog


class ExternalSkillCatalogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "SKILL.md"
        self.source.write_text(
            "---\nname: test-research\ndescription: Research guidance\n"
            "version: \"1.2.0\"\nlicense: Apache-2.0\n"
            "metadata:\n  category: Research\n  tags: [search, analysis]\n"
            "---\nUse reliable sources and cite evidence.\n",
            encoding="utf-8",
        )
        self.catalog = ExternalSkillCatalog(self.root / "catalog")

    def stage(self):
        return self.catalog.stage(
            self.source, skill_id="google.test-research",
            repository="google/skills",
            source_path="skills/research/test-research/SKILL.md",
            commit="a" * 40, license_id="Apache-2.0",
        )

    def test_stage_quarantines_and_preserves_metadata_and_provenance(self):
        record = self.stage()
        self.assertEqual(record["trust_state"], "quarantined")
        self.assertEqual(record["upstream_metadata"]["category"], "Research")
        self.assertEqual(record["source"]["repository"], "google/skills")
        self.assertEqual(record["source"]["commit"], "a" * 40)
        self.assertEqual(len(record["source"]["content_sha256"]), 64)
        self.assertIsNone(record["review"])
        self.assertNotIn("instructions", self.catalog.list()[0])

    def test_stage_is_idempotent_for_same_digest_and_rejects_changed_content(self):
        first = self.stage()
        second = self.stage()
        self.assertEqual(first["source"]["content_sha256"], second["source"]["content_sha256"])
        self.source.write_text(self.source.read_text() + "\nChanged.\n", encoding="utf-8")
        with self.assertRaises(FileExistsError):
            self.stage()

    def test_quarantine_requires_explicit_security_and_license_review(self):
        self.stage()
        with self.assertRaises(PermissionError):
            self.catalog.review("google.test-research", reviewer="owner",
                                security_reviewed=True, license_reviewed=True,
                                note="Reviewed", confirm=False)
        with self.assertRaises(PermissionError):
            self.catalog.review("google.test-research", reviewer="owner",
                                security_reviewed=True, license_reviewed=False,
                                note="Reviewed", confirm=True)

    def test_only_reviewed_skill_can_be_enabled(self):
        self.stage()
        with self.assertRaises(PermissionError):
            self.catalog.enable("google.test-research", actor="owner", confirm=True)
        self.catalog.review("google.test-research", reviewer="owner",
                            security_reviewed=True, license_reviewed=True,
                            note="Static review complete", confirm=True)
        with self.assertRaises(PermissionError):
            self.catalog.enable("google.test-research", actor="owner", confirm=False)
        enabled = self.catalog.enable("google.test-research", actor="owner", confirm=True)
        self.assertEqual(enabled["trust_state"], "enabled")
        self.assertEqual([a["action"] for a in enabled["audit"]],
                         ["staged", "reviewed", "enabled"])

    def test_disable_and_revoke_are_audited(self):
        self.stage()
        self.catalog.review("google.test-research", reviewer="owner",
                            security_reviewed=True, license_reviewed=True,
                            note="Reviewed", confirm=True)
        self.catalog.enable("google.test-research", actor="owner", confirm=True)
        disabled = self.catalog.disable("google.test-research", actor="owner", confirm=True)
        self.assertEqual(disabled["trust_state"], "disabled")
        revoked = self.catalog.revoke("google.test-research", actor="owner", confirm=True)
        self.assertEqual(revoked["trust_state"], "revoked")

    def test_unpinned_or_unsafe_source_metadata_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "immutable_git_sha"):
            self.catalog.stage(
                self.source, skill_id="google.test-research",
                repository="google/skills", source_path="SKILL.md",
                commit="main", license_id="Apache-2.0",
            )
        with self.assertRaisesRegex(ValueError, "contained"):
            self.catalog.stage(
                self.source, skill_id="google.test-research",
                repository="google/skills", source_path="../SKILL.md",
                commit="a" * 40, license_id="Apache-2.0",
            )

    def test_duplicate_json_keys_in_catalog_are_rejected(self):
        self.stage()
        path = next((self.root / "catalog").glob("*.json"))
        path.write_text('{"schema_version":1,"schema_version":1}', encoding="utf-8")
        self.assertEqual(self.catalog.list(), [])
        with self.assertRaisesRegex(ValueError, "invalid"):
            self.catalog.get("google.test-research")


if __name__ == "__main__":
    unittest.main()
