import unittest

from core.skill_manifest import SkillManifest


def manifest(**overrides):
    value = {
        "skill_id": "workspace.read_file",
        "version": "1.0.0",
        "entrypoint": "skills.files.ReadFileSkill",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string", "maxLength": 200}},
            "required": ["path"],
            "additionalProperties": False,
        },
        "output_schema": {
            "type": "object",
            "properties": {"text": {"type": "string", "maxLength": 2000}},
            "required": ["text"],
            "additionalProperties": False,
        },
        "capabilities": ["workspace.read"],
        "risk": "low",
        "timeout_seconds": 10,
        "max_output_bytes": 4096,
        "parallel_safe": True,
        "resource_group": "workspace_read",
    }
    value.update(overrides)
    return value


class SkillManifestTests(unittest.TestCase):
    def test_valid_manifest_validates_input_and_output(self):
        skill = SkillManifest.from_dict(manifest())
        skill.validate_input({"path": "notes.txt"})
        skill.validate_output({"text": "safe"})
        self.assertEqual(skill.skill_id, "workspace.read_file")

    def test_rejects_unknown_capability(self):
        with self.assertRaises(ValueError):
            SkillManifest.from_dict(manifest(capabilities=["root.access"]))

    def test_rejects_side_effecting_parallel_safe_manifest(self):
        with self.assertRaises(ValueError):
            SkillManifest.from_dict(manifest(capabilities=["workspace.write"], parallel_safe=True))

    def test_rejects_unknown_manifest_fields(self):
        with self.assertRaises(ValueError):
            SkillManifest.from_dict(manifest(shell_command="rm -rf /"))

    def test_input_schema_rejects_extra_fields_and_wrong_types(self):
        skill = SkillManifest.from_dict(manifest())
        with self.assertRaises(ValueError):
            skill.validate_input({"path": "a.txt", "shell": "whoami"})
        with self.assertRaises(ValueError):
            skill.validate_input({"path": 123})

    def test_output_schema_enforces_size_and_shape(self):
        skill = SkillManifest.from_dict(manifest())
        with self.assertRaises(ValueError):
            skill.validate_output({"text": "x" * 3000})
        with self.assertRaises(ValueError):
            skill.validate_output({"text": "ok", "secret": "extra"})

    def test_rejects_unbounded_or_invalid_schemas(self):
        bad = manifest()
        bad["input_schema"] = {"type": "object", "properties": {"x": {"type": "code"}}}
        with self.assertRaises(ValueError):
            SkillManifest.from_dict(bad)

    def test_parallel_safe_is_type_checked(self):
        with self.assertRaises(ValueError):
            SkillManifest.from_dict(manifest(parallel_safe="yes"))


if __name__ == "__main__":
    unittest.main()
