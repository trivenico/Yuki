"""Phase 7 regression tests for manifests and bounded skill subprocesses."""
from __future__ import annotations
import sys
import unittest
from core.skill_manifest import SkillManifest
from core.subprocess_executor import SubprocessExecutor

def manifest(**overrides):
    value = {
        "skill_id": "test.echo", "version": "1.0.0", "entrypoint": "worker.run",
        "input_schema": {"type": "object", "properties": {"text": {"type": "string", "maxLength": 20}},
                         "required": ["text"], "additionalProperties": False},
        "output_schema": {"type": "object", "properties": {"saved": {"type": "boolean"}},
                          "required": ["saved"], "additionalProperties": False},
        "capabilities": ["workspace.write"], "risk": "low",
        "timeout_seconds": 2, "max_output_bytes": 1024,
    }
    value.update(overrides)
    return value

class ManifestTests(unittest.TestCase):
    def test_rejects_invalid_identity_and_limits(self):
        for changes in ({"skill_id": "../escape"}, {"version": "latest"},
                        {"timeout_seconds": 0}, {"max_output_bytes": 0},
                        {"capabilities": ["workspace.write", "workspace.write"]}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                SkillManifest.from_dict(manifest(**changes))

    def test_input_schema_rejects_unknown_and_oversized_fields(self):
        parsed = SkillManifest.from_dict(manifest())
        self.assertTrue(parsed.validate_object(parsed.input_schema, {"text": "ok"}))
        self.assertFalse(parsed.validate_object(parsed.input_schema, {"text": "x" * 21}))
        self.assertFalse(parsed.validate_object(parsed.input_schema, {"text": "ok", "extra": True}))

class SubprocessExecutorTests(unittest.TestCase):
    def test_accepts_bounded_json_output(self):
        executor = SubprocessExecutor(timeout_seconds=2, max_output_bytes=1024)
        result = executor.run([sys.executable, "-c", "import json; print(json.dumps({'ok': True}))"], {})
        self.assertIs(result["ok"], True)

    def test_kills_process_when_output_exceeds_limit(self):
        executor = SubprocessExecutor(timeout_seconds=3, max_output_bytes=128)
        with self.assertRaisesRegex(RuntimeError, "output exceeded"):
            executor.run([sys.executable, "-c", "print('x' * 10000)"], {})

    def test_timeout_is_enforced(self):
        executor = SubprocessExecutor(timeout_seconds=1, max_output_bytes=128)
        with self.assertRaisesRegex(RuntimeError, "timed out"):
            executor.run([sys.executable, "-c", "import time; time.sleep(3)"], {})

if __name__ == "__main__":
    unittest.main()
