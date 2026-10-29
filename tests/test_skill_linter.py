import tempfile
import unittest
from pathlib import Path

from core.skill_linter import findings_as_json, lint_content, lint_skill


class SkillLinterTests(unittest.TestCase):
    def test_good_skill_has_no_errors_and_reports_advisory_metadata(self):
        content = (
            "---\nname: release-notes\ndescription: Draft concise release notes.\n"
            "license: MIT\ncompatibility: Windows and Linux\nmetadata: {}\n---\n"
            "## When to Use\nUse this when preparing verified release notes.\n"
        )
        findings = lint_content(content, skill_dir=Path("release-notes"))
        report = findings_as_json(findings)
        self.assertTrue(report["valid"])
        self.assertEqual(report["errors"], 0)
        self.assertEqual(report["warnings"], 0)

    def test_flags_missing_fields_marketing_and_missing_trigger_section(self):
        content = (
            "---\nname: ReleaseNotes\ndescription: A powerful comprehensive helper.\n---\n"
            "Do the work.\n"
        )
        rules = {finding.rule for finding in lint_content(content)}
        self.assertIn("name-format", rules)
        self.assertIn("description-marketing", rules)
        self.assertIn("missing-section", rules)
        self.assertIn("missing-metadata", rules)

    def test_duplicate_frontmatter_keys_fail_closed(self):
        content = "---\nname: first\nname: second\ndescription: Draft notes.\n---\nBody.\n"
        findings = lint_content(content)
        self.assertEqual([finding.rule for finding in findings], ["invalid-frontmatter"])

    def test_ignores_shell_utility_examples_inside_fenced_code(self):
        content = (
            "---\nname: dev-guide\ndescription: Explain developer workflows.\n"
            "license: MIT\ncompatibility: all\nmetadata: {}\n---\n"
            "## When to Use\nExample:\n```sh\ncat README.md\n```\n"
        )
        rules = {finding.rule for finding in lint_content(content)}
        self.assertNotIn("shell-utility-reference", rules)

    def test_detects_dangling_references_and_forbidden_scaffolding(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "release-notes"
            root.mkdir()
            (root / "SKILL.md").write_text(
                "---\nname: release-notes\ndescription: Draft release notes.\n"
                "license: MIT\ncompatibility: Windows\nmetadata: {}\n---\n"
                "## When to Use\nRead references/missing.md before starting.\n",
                encoding="utf-8",
            )
            (root / "README.md").write_text("scaffolding", encoding="utf-8")
            rules = {finding.rule for finding in lint_skill(root / "SKILL.md")}
            self.assertIn("dangling-reference", rules)
            self.assertIn("forbidden-file", rules)

    def test_does_not_inspect_symlinked_scripts_outside_skill(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "skill"
            scripts = root / "scripts"
            scripts.mkdir(parents=True)
            outside = Path(temp) / "outside.sh"
            outside.write_text("import fcntl\n", encoding="utf-8")
            try:
                (scripts / "external.sh").symlink_to(outside)
            except (OSError, NotImplementedError):
                self.skipTest("Symlink creation is unavailable")
            skill = root / "SKILL.md"
            skill.write_text(
                "---\nname: skill\ndescription: Inspect scripts.\n"
                "license: MIT\ncompatibility: local\nmetadata: {}\n---\n"
                "## When to Use\nReview scripts safely.\n",
                encoding="utf-8",
            )
            rules = {finding.rule for finding in lint_skill(skill)}
            self.assertNotIn("platforms-gating", rules)

    def test_rejects_symlink_and_oversized_skill(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "SKILL.md"
            source.write_text("---\nname: a\ndescription: Test.\n---\nBody.\n", encoding="utf-8")
            linked = root / "LINK.md"
            try:
                linked.symlink_to(source)
            except (OSError, NotImplementedError):
                pass
            else:
                self.assertEqual(lint_skill(linked)[0].rule, "invalid-path")
            source.write_text("x" * (64 * 1024 + 1), encoding="utf-8")
            self.assertEqual(lint_skill(source)[0].rule, "file-too-large")


if __name__ == "__main__":
    unittest.main()
