import hashlib
import json
import unittest

from core.skill_schema_validation import SkillSchemaError, load_verified_schema, validate_instance


class SkillSchemaValidationTests(unittest.TestCase):
    def _load(self, schema):
        raw = json.dumps(schema, sort_keys=True, separators=(",", ":")).encode()
        return load_verified_schema(raw, hashlib.sha256(raw).hexdigest())

    def test_validates_schema_constrained_input_and_output(self):
        schema = self._load({
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "properties": {"format": {"type": "string", "enum": ["json", "csv"]}},
            "required": ["format"],
            "additionalProperties": False,
        })
        validate_instance({"format": "json"}, schema)
        with self.assertRaises(SkillSchemaError):
            validate_instance({"format": "shell", "extra": True}, schema)

    def test_rejects_digest_mismatch_and_remote_refs(self):
        raw = b'{"type":"object"}'
        with self.assertRaises(SkillSchemaError):
            load_verified_schema(raw, "0"*64)
        remote = b'{"$ref":"https://example.invalid/schema.json"}'
        with self.assertRaises(SkillSchemaError):
            load_verified_schema(remote, hashlib.sha256(remote).hexdigest())

    def test_rejects_duplicate_keys_invalid_schema_and_oversize(self):
        raw = b'{"type":"object","type":"string"}'
        with self.assertRaises(SkillSchemaError):
            load_verified_schema(raw, hashlib.sha256(raw).hexdigest())
        invalid = b'{"type":"not-a-json-schema-type"}'
        with self.assertRaises(SkillSchemaError):
            load_verified_schema(invalid, hashlib.sha256(invalid).hexdigest())
        oversized = b" " * 262145
        with self.assertRaises(SkillSchemaError):
            load_verified_schema(oversized, hashlib.sha256(oversized).hexdigest())


if __name__ == "__main__":
    unittest.main()
