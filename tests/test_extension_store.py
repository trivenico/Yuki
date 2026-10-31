import base64
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.extension_store import (
    InstalledPromptSkill,
    _atomic_json,
    install_extension,
    install_github_skill,
    install_skill_file,
    list_extensions,
    plan_installed_skill_dependencies,
    load_installed_mcp_servers,
    load_installed_skills,
    remove_extension,
    validate_plugin_manifest,
    validate_skill_manifest,
)


class ExtensionStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "source"
        self.source.mkdir()
        self.data = self.root / "data"

    def tearDown(self):
        self.temp.cleanup()

    def write_manifest(self, name, value):
        path = self.source / name
        path.write_text(json.dumps(value), encoding="utf-8")
        return path

    def skill_manifest(self, **overrides):
        value = {
            "type": "yuki_skill",
            "id": "writing_helper",
            "version": "1",
            "name": "writing_helper",
            "description": "Help draft and revise text.",
            "instructions": "Ask for the audience and desired tone. Preserve factual accuracy.",
        }
        value.update(overrides)
        return value

    def plugin_manifest(self, **overrides):
        value = {
            "type": "yuki_mcp_plugin",
            "id": "example_docs",
            "version": "1",
            "name": "example_docs",
            "endpoint": "https://mcp.example.com/api",
            "allowed_hosts": ["mcp.example.com"],
            "allowed_tools": ["search_docs", "read_doc"],
        }
        value.update(overrides)
        return value

    def test_atomic_extension_publish_never_overwrites_existing_file(self):
        destination = self.data / "installed_skills" / "example.json"
        destination.parent.mkdir(parents=True)
        destination.write_text('{"name":"original"}', encoding="utf-8")

        with self.assertRaises(FileExistsError):
            _atomic_json(destination, {"name": "replacement"})

        self.assertEqual(
            json.loads(destination.read_text(encoding="utf-8")),
            {"name": "original"},
        )

    def test_install_load_execute_and_remove_declarative_skill(self):
        source = self.write_manifest("skill.json", self.skill_manifest())
        installed = install_extension(str(source), "skill", root=self.data)
        self.assertEqual(installed["id"], "writing_helper")
        skills = load_installed_skills(root=self.data)
        self.assertEqual(len(skills), 1)
        self.assertIsInstance(skills[0], InstalledPromptSkill)
        result = skills[0].execute("draft a release note")
        self.assertTrue(result.ok)
        self.assertIn("Preserve factual accuracy", result.message)
        self.assertIn("draft a release note", result.message)
        self.assertTrue(remove_extension("writing_helper", "skill", root=self.data))
        self.assertEqual(load_installed_skills(root=self.data), [])

    def test_rejects_unknown_fields_and_non_json_skill_payloads(self):
        value = self.skill_manifest()
        value["python_module"] = "os"
        with self.assertRaisesRegex(ValueError, "unsupported fields"):
            validate_skill_manifest(value)
        value = self.skill_manifest(instructions="x" * 40001)
        with self.assertRaisesRegex(ValueError, "instructions"):
            validate_skill_manifest(value)

    def test_installed_skill_dependency_plan_reads_manifests_without_installing_packages(self):
        first = self.write_manifest("first.json", self.skill_manifest(
            dependencies={"skills": [], "python": [{"name": "PyYAML", "version": ">=6.0,<7.0"}]},
            skill_version="1.2.0",
        ))
        install_extension(str(first), "skill", root=self.data)
        plan = plan_installed_skill_dependencies(
            "writing_helper", root=self.data, python_packages={"pyyaml": "6.0.2"}
        )
        self.assertTrue(plan.ready)
        self.assertEqual(plan.installation_order, ("writing_helper",))
        self.assertFalse(plan.as_dict()["package_installation_performed"])
        listing = list_extensions("skill", root=self.data)
        self.assertEqual(listing[0]["skill_version"], "1.2.0")
        self.assertEqual(listing[0]["dependency_count"], 1)

    def test_installed_skill_dependency_plan_reports_missing_packages(self):
        source = self.write_manifest("first.json", self.skill_manifest(
            dependencies={"skills": [], "python": [{"name": "requests", "version": ">=2.0"}]},
        ))
        install_extension(str(source), "skill", root=self.data)
        plan = plan_installed_skill_dependencies("writing_helper", root=self.data, python_packages={})
        self.assertFalse(plan.ready)
        self.assertEqual(plan.missing_python, ("requests",))

    def test_imports_agent_skills_markdown_as_non_executable_prompt_skill(self):
        source = self.source / "SKILL.md"
        source.write_text(
            "---\nname: release-notes\ndescription: Draft accurate release notes.\n---\n"
            "Ask for the release audience, summarize verified changes, and do not invent claims.\n",
            encoding="utf-8",
        )
        installed = install_skill_file(str(source), root=self.data)
        self.assertEqual(installed["source_format"], "agentskills_md")
        skills = load_installed_skills(root=self.data)
        self.assertEqual(len(skills), 1)
        self.assertEqual(skills[0].manifest["id"], "release_notes")
        self.assertIn("do not invent claims", skills[0].manifest["instructions"])

    def test_imports_agent_skills_directory_containing_skill_md(self):
        skill_dir = self.source / "release-notes"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            "---\nname: release-notes\ndescription: Draft accurate release notes.\n---\n"
            "Summarize verified changes and preserve factual accuracy.\n",
            encoding="utf-8",
        )
        # A companion script is deliberately ignored and never executed.
        script = skill_dir / "install.py"
        script.write_text("raise RuntimeError('must not execute')\n", encoding="utf-8")

        installed = install_skill_file(str(skill_dir), root=self.data)
        self.assertEqual(installed["id"], "release_notes")
        skills = load_installed_skills(root=self.data)
        self.assertEqual(len(skills), 1)
        self.assertIn("preserve factual accuracy", skills[0].manifest["instructions"])

    def test_imported_skill_defaults_to_medium_risk_and_requires_approval(self):
        source = self.source / "SKILL.md"
        source.write_text(
            "---\nname: release-notes\ndescription: Draft accurate release notes.\n---\n"
            "Summarize verified changes and preserve factual accuracy.\n",
            encoding="utf-8",
        )
        installed = install_skill_file(str(source), root=self.data)
        self.assertEqual(installed["risk"], "medium")
        self.assertTrue(installed["requires_invocation_approval"])
        skill = load_installed_skills(root=self.data)[0]
        self.assertEqual(skill.risk, "medium")
        self.assertTrue(skill.requires_approval)

    def test_imported_skill_invocation_uses_existing_one_shot_approval_guard(self):
        from core.guarded_skill_runtime import OneShotSkillApprovals, invoke_registered_skill

        source = self.source / "SKILL.md"
        source.write_text(
            "---\nname: release-notes\ndescription: Draft accurate release notes.\n---\n"
            "Summarize verified changes and preserve factual accuracy.\n",
            encoding="utf-8",
        )
        install_skill_file(str(source), root=self.data)
        skill = load_installed_skills(root=self.data)[0]

        class Registry:
            def get(self, name):
                return skill if name == skill.name else None

        approvals = OneShotSkillApprovals()
        denied = invoke_registered_skill(Registry(), skill.name, "release v1", approvals=approvals)
        self.assertEqual(denied.status, "approval_required")
        approvals.approve(skill.name, "release v1")
        allowed = invoke_registered_skill(Registry(), skill.name, "release v1", approvals=approvals)
        self.assertTrue(allowed.ok)
        replay = invoke_registered_skill(Registry(), skill.name, "release v1", approvals=approvals)
        self.assertEqual(replay.status, "approval_required")

    def test_offensive_skill_content_is_classified_high_risk(self):
        source = self.source / "SKILL.md"
        source.write_text(
            "---\nname: red-team\ndescription: Offensive red team methodology.\n---\n"
            "Assess authorized targets for exploit development, credential harvesting, "
            "privilege escalation, persistence, and exfiltration.\n",
            encoding="utf-8",
        )
        installed = install_skill_file(str(source), root=self.data)
        self.assertEqual(installed["risk"], "high")
        skill = load_installed_skills(root=self.data)[0]
        self.assertEqual(skill.risk, "high")
        self.assertTrue(skill.requires_approval)

    def test_skill_manifest_rejects_unknown_risk_level(self):
        with self.assertRaisesRegex(ValueError, "risk"):
            validate_skill_manifest(self.skill_manifest(risk="unrestricted"))

    def test_agent_skills_directory_import_rejects_symlinked_skill_file(self):
        skill_dir = self.source / "linked-skill"
        skill_dir.mkdir()
        outside = self.source / "outside.md"
        outside.write_text(
            "---\nname: linked_skill\ndescription: Valid description.\n---\nInstructions.\n",
            encoding="utf-8",
        )
        try:
            (skill_dir / "SKILL.md").symlink_to(outside)
        except (OSError, NotImplementedError):
            self.skipTest("Symlink creation is not available in this environment")
        with self.assertRaisesRegex(ValueError, "symbolic link"):
            install_skill_file(str(skill_dir), root=self.data)

    def test_agent_skills_import_rejects_non_skill_directory_file(self):
        skill_dir = self.source / "missing-skill"
        skill_dir.mkdir()
        (skill_dir / "README.md").write_text("not a skill", encoding="utf-8")
        with self.assertRaises(ValueError):
            install_skill_file(str(skill_dir), root=self.data)

    def test_github_skill_install_requires_approval_and_records_pinned_source(self):
        document = (
            "---\nname: design-taste-frontend\ndescription: Improve frontend visual quality.\n---\n"
            + ("Make deliberate design decisions and test the page.\n" * 700)
        )
        commit = "a" * 40
        tree = {
            "truncated": False,
            "tree": [
                {"path": "skills/taste-skill/SKILL.md", "type": "blob"},
                {"path": "skills/taste-skill/references/type.md", "type": "blob"},
                {"path": "skills/taste-skill/scripts/draw.mjs", "type": "blob"},
                {"path": "README.md", "type": "blob"},
                {"path": "LICENSE", "type": "blob"},
            ],
        }
        def fake_api(url, *, max_bytes=5 * 1024 * 1024):
            if url.endswith("/repos/Leonxlnx/taste-skill"):
                return {"default_branch": "main", "license": {"spdx_id": "MIT"}}
            if "/commits/" in url:
                return {"sha": commit}
            if "/git/trees/" in url:
                return tree
            if "/contents/skills/taste-skill/SKILL.md?ref=" in url:
                return {
                    "type": "file",
                    "encoding": "base64",
                    "content": base64.b64encode(document.encode()).decode(),
                }
            raise AssertionError(f"Unexpected GitHub API URL: {url}")

        with patch("core.extension_store._github_api_json", side_effect=fake_api):
            cancelled = install_github_skill(
                "Leonxlnx/taste-skill", root=self.data, approve=lambda preview: False
            )
        self.assertFalse(cancelled["installed"])
        self.assertEqual(cancelled["preview"]["risk"], "medium")
        self.assertTrue(cancelled["preview"]["requires_invocation_approval"])
        self.assertIn("lint_report", cancelled["preview"])
        self.assertEqual(cancelled["preview"]["license"], "MIT")
        self.assertEqual(cancelled["preview"]["license_file"], "LICENSE")
        self.assertIn("skills/taste-skill/scripts/draw.mjs", cancelled["preview"]["executable_script_candidates"])
        self.assertEqual(load_installed_skills(root=self.data), [])

        with patch("core.extension_store._github_api_json", side_effect=fake_api):
            installed = install_github_skill(
                "Leonxlnx/taste-skill", root=self.data, approve=lambda preview: True
            )
        self.assertEqual(installed["source_commit"], commit)
        self.assertEqual(installed["source_path"], "skills/taste-skill/SKILL.md")
        self.assertEqual(installed["compatibility"], "instructions-only")
        self.assertEqual(installed["risk"], "medium")
        installed_manifest = json.loads(next((self.data / "installed_skills").glob("*.json")).read_text(encoding="utf-8"))
        self.assertEqual(installed_manifest["source_license"], "MIT")
        self.assertEqual(installed_manifest["source_license_path"], "LICENSE")
        skills = load_installed_skills(root=self.data)
        self.assertEqual(len(skills), 1)
        self.assertEqual(len(skills[0].manifest["instructions"]), len(document.split("---\n", 2)[-1].strip()))

    def test_github_skill_install_rejects_invalid_repository_and_ambiguous_skills(self):
        with self.assertRaisesRegex(ValueError, "OWNER/REPO"):
            install_github_skill("../evil/repo", root=self.data, approve=lambda _: True)

        def fake_api(url, *, max_bytes=5 * 1024 * 1024):
            if url.endswith("/repos/owner/repo"):
                return {"default_branch": "main"}
            if "/commits/" in url:
                return {"sha": "b" * 40}
            if "/git/trees/" in url:
                return {"truncated": False, "tree": [
                    {"path": "one/SKILL.md", "type": "blob"},
                    {"path": "two/SKILL.md", "type": "blob"},
                ]}
            raise AssertionError(f"Unexpected GitHub API URL: {url}")

        with patch("core.extension_store._github_api_json", side_effect=fake_api):
            with self.assertRaisesRegex(ValueError, "Choose a skill with --path"):
                install_github_skill("owner/repo", root=self.data, approve=lambda _: True)

    def test_agent_skills_import_rejects_excessively_nested_frontmatter(self):
        source = self.source / "SKILL.md"
        nested = "[" * 2000 + "x" + "]" * 2000
        source.write_text(
            "---\nname: valid_skill\ndescription: valid description\nmetadata: " + nested + "\n---\nInstructions.\n",
            encoding="utf-8",
        )
        with self.assertRaises(ValueError):
            install_skill_file(str(source), root=self.data)

    def test_agent_skills_import_rejects_yaml_aliases(self):
        source = self.source / "SKILL.md"
        source.write_text(
            "---\nname: valid_skill\ndescription: &desc valid description\nmetadata: *desc\n---\nInstructions.\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(ValueError, "valid YAML"):
            install_skill_file(str(source), root=self.data)

    def test_agent_skills_import_rejects_duplicate_yaml_keys(self):
        source = self.source / "SKILL.md"
        source.write_text(
            "---\nname: first_name\nname: second_name\ndescription: valid description\n---\nInstructions.\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(ValueError, "Duplicate YAML frontmatter key"):
            install_skill_file(str(source), root=self.data)

    def test_agent_skills_import_rejects_non_skill_md_and_unclosed_frontmatter(self):
        wrong_name = self.source / "README.md"
        wrong_name.write_text("---\nname: valid_skill\ndescription: valid description\n---\ntext", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "named SKILL.md"):
            install_skill_file(str(wrong_name), root=self.data)
        source = self.source / "SKILL.md"
        source.write_text("---\nname: valid_skill\ndescription: valid description\nno closing delimiter", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "not closed"):
            install_skill_file(str(source), root=self.data)

    def test_excessively_nested_json_is_rejected_without_crashing(self):
        path = self.source / "nested.json"
        path.write_text("[" * 2000 + "0" + "]" * 2000, encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "valid UTF-8 JSON"):
            install_extension(str(path), "skill", root=self.data)

    def test_duplicate_json_keys_are_rejected(self):
        path = self.source / "duplicate.json"
        path.write_text(
            '{"type":"yuki_skill","id":"first_skill","id":"other_skill",'
            '"version":"1","name":"writing_helper","description":"desc",'
            '"instructions":"instructions"}',
            encoding="utf-8",
        )
        with self.assertRaisesRegex(ValueError, "Duplicate JSON key"):
            install_extension(str(path), "skill", root=self.data)

    def test_plugin_endpoint_must_be_https_and_match_allowlisted_host(self):
        with self.assertRaisesRegex(ValueError, "HTTPS"):
            validate_plugin_manifest(self.plugin_manifest(endpoint="http://mcp.example.com/api"))
        with self.assertRaisesRegex(ValueError, "appear in allowed_hosts"):
            validate_plugin_manifest(self.plugin_manifest(endpoint="https://evil.example/api"))
        with self.assertRaisesRegex(ValueError, "credentials"):
            validate_plugin_manifest(self.plugin_manifest(endpoint="https://user:secret@mcp.example.com/api"))

    def test_plugin_endpoint_rejects_surrounding_whitespace_and_control_characters(self):
        for endpoint in (
            " https://mcp.example.com/api",
            "https://mcp.example.com/api ",
            "https://mcp.example.com/api" + chr(10),
        ):
            with self.subTest(endpoint=repr(endpoint)):
                with self.assertRaisesRegex(ValueError, "whitespace or control"):
                    validate_plugin_manifest(self.plugin_manifest(endpoint=endpoint))

    def test_plugin_endpoint_rejects_malformed_or_invalid_ports(self):
        for endpoint in (
            "https://mcp.example.com:bad/api",
            "https://mcp.example.com:65536/api",
            "https://mcp.example.com:0/api",
        ):
            with self.subTest(endpoint=endpoint):
                with self.assertRaisesRegex(ValueError, "port"):
                    validate_plugin_manifest(self.plugin_manifest(endpoint=endpoint))

    def test_plugin_endpoint_rejects_non_default_https_ports(self):
        for endpoint in (
            "https://mcp.example.com:8443/api",
            "https://mcp.example.com:444/api",
        ):
            with self.subTest(endpoint=endpoint):
                with self.assertRaisesRegex(ValueError, "default HTTPS port"):
                    validate_plugin_manifest(self.plugin_manifest(endpoint=endpoint))

    def test_plugin_endpoint_rejects_invalid_dns_hostname_labels(self):
        for host in ("-mcp.example.com", "mcp-.example.com", "mcp..example.com"):
            with self.subTest(host=host):
                manifest = self.plugin_manifest(
                    endpoint=f"https://{host}/api",
                    allowed_hosts=[host],
                )
                with self.assertRaisesRegex(ValueError, "allowed_hosts"):
                    validate_plugin_manifest(manifest)

    def test_plugin_install_exposes_only_allowlisted_configuration(self):
        source = self.write_manifest("plugin.json", self.plugin_manifest())
        installed = install_extension(str(source), "plugin", root=self.data)
        self.assertEqual(installed["id"], "example_docs")
        servers = load_installed_mcp_servers(root=self.data)
        self.assertEqual(len(servers), 1)
        self.assertEqual(servers[0]["allowed_tools"], ["search_docs", "read_doc"])
        self.assertEqual(list_extensions("plugin", root=self.data)[0]["valid"], True)
        self.assertTrue(remove_extension("example_docs", "plugin", root=self.data))
        self.assertEqual(load_installed_mcp_servers(root=self.data), [])

    def test_skill_import_rejects_symlinked_parent_directory(self):
        real_dir = self.root / "real-skill"
        real_dir.mkdir()
        skill_file = real_dir / "SKILL.md"
        skill_file.write_text(
            "---\nname: safe-skill\ndescription: A safe test skill.\n---\n"
            "## When to Use\nUse this skill for a test.\n",
            encoding="utf-8",
        )
        linked_dir = self.root / "linked-skill"
        try:
            linked_dir.symlink_to(real_dir, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("Directory symlink creation is unavailable")
        with self.assertRaisesRegex(ValueError, "parent directories"):
            install_skill_file(str(linked_dir / "SKILL.md"), root=self.data)

    def test_install_rejects_symlinked_manifest_source(self):
        source = self.write_manifest("real.json", self.skill_manifest())
        linked = self.source / "linked.json"
        try:
            linked.symlink_to(source)
        except (OSError, NotImplementedError):
            self.skipTest("Symlink creation is unavailable")
        with self.assertRaisesRegex(ValueError, "symbolic link"):
            install_extension(str(linked), "skill", root=self.data)

    def test_installed_skill_loader_ignores_symlinked_manifest(self):
        folder = self.data / "installed_skills"
        folder.mkdir(parents=True)
        outside = self.write_manifest("outside.json", self.skill_manifest())
        try:
            (folder / "linked.json").symlink_to(outside)
        except (OSError, NotImplementedError):
            self.skipTest("Symlink creation is unavailable")
        self.assertEqual(load_installed_skills(root=self.data), [])
        listing = list_extensions("skill", root=self.data)
        self.assertEqual(len(listing), 1)
        self.assertFalse(listing[0]["valid"])

    def test_extension_store_directory_cannot_be_redirected_by_symlink(self):
        outside = self.root / "outside-store"
        outside.mkdir()
        self.data.mkdir()
        try:
            (self.data / "installed_skills").symlink_to(outside, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("Directory symlink creation is unavailable")
        with self.assertRaisesRegex(ValueError, "directory must not be a symbolic link"):
            load_installed_skills(root=self.data)

    def test_duplicate_id_cannot_overwrite_installed_skill(self):
        source = self.write_manifest("skill.json", self.skill_manifest())
        install_extension(str(source), "skill", root=self.data)
        source.write_text(json.dumps(self.skill_manifest(instructions="replace")), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "already installed"):
            install_extension(str(source), "skill", root=self.data)


if __name__ == "__main__":
    unittest.main()
