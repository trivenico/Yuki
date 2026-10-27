import json
import tempfile
import unittest
from pathlib import Path

from core.extension_store import (
    InstalledPromptSkill,
    install_extension,
    list_extensions,
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
        with self.assertRaisesRegex(ValueError, "only"):
            validate_skill_manifest(value)
        value = self.skill_manifest(instructions="x" * 12001)
        with self.assertRaisesRegex(ValueError, "instructions"):
            validate_skill_manifest(value)

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

    def test_duplicate_id_cannot_overwrite_installed_skill(self):
        source = self.write_manifest("skill.json", self.skill_manifest())
        install_extension(str(source), "skill", root=self.data)
        source.write_text(json.dumps(self.skill_manifest(instructions="replace")), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "already installed"):
            install_extension(str(source), "skill", root=self.data)


if __name__ == "__main__":
    unittest.main()
