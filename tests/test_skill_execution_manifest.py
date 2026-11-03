import hashlib
import json
import unittest

from core.skill_execution_manifest import (
    SkillManifestError, canonical_json_bytes, manifest_sha256,
    parse_manifest_json, validate_manifest, verify_artifact_digest,
)


class SkillExecutionManifestTests(unittest.TestCase):
    def setUp(self):
        self.value = {
            "protocol_version": 1,
            "skill_id": "report_builder",
            "skill_version": "1.0.0",
            "artifact_sha256": "a" * 64,
            "entrypoint": "package.entry:run",
            "input_schema_sha256": "b" * 64,
            "output_schema_sha256": "c" * 64,
            "runtime_image": "registry.example/yuki-runtime@sha256:" + "d" * 64,
            "environment_lock_sha256": "e" * 64,
            "capabilities": ["read_staged_input"],
            "resource_limits": {
                "timeout_seconds": 30, "memory_mb": 256, "cpu_millis": 500,
                "max_processes": 16, "max_input_bytes": 65536,
                "max_output_bytes": 262144,
            },
            "network": "none",
            "mount_policy": "invocation_staging_only",
        }

    def test_valid_manifest_is_immutable_data_and_has_stable_digest(self):
        manifest = validate_manifest(self.value)
        self.assertEqual(manifest.capabilities, ("read_staged_input",))
        self.assertEqual(manifest_sha256(manifest), manifest_sha256(validate_manifest(dict(self.value))))
        self.assertEqual(manifest.as_dict()["network"], "none")

    def test_parser_rejects_duplicate_keys_and_nonfinite_values(self):
        raw = json.dumps(self.value)
        with self.assertRaises(SkillManifestError):
            parse_manifest_json(raw[:-1] + ',"network":"allowlist"}')
        with self.assertRaises(SkillManifestError):
            parse_manifest_json('{"bad":NaN}')

    def test_rejects_unknown_missing_and_mutable_fields(self):
        for mutate in (
            lambda v: v.update(extra=True),
            lambda v: v.pop("artifact_sha256"),
            lambda v: v.update(runtime_image="python:3.12"),
            lambda v: v.update(network="allowlist"),
            lambda v: v.update(mount_policy="workspace"),
            lambda v: v.update(entrypoint="../evil:run"),
        ):
            candidate = dict(self.value)
            mutate(candidate)
            with self.subTest(candidate=candidate), self.assertRaises(SkillManifestError):
                validate_manifest(candidate)

    def test_rejects_capability_and_resource_tampering(self):
        candidate = dict(self.value)
        candidate["capabilities"] = ["write_staged_output", "read_staged_input"]
        with self.assertRaises(SkillManifestError):
            validate_manifest(candidate)
        candidate = dict(self.value)
        candidate["resource_limits"] = dict(self.value["resource_limits"], timeout_seconds=True)
        with self.assertRaises(SkillManifestError):
            validate_manifest(candidate)
        candidate = dict(self.value)
        candidate["resource_limits"] = dict(self.value["resource_limits"], max_processes=0)
        with self.assertRaises(SkillManifestError):
            validate_manifest(candidate)

    def test_rejects_invalid_digest_and_protocol(self):
        candidate = dict(self.value, artifact_sha256="A" * 64)
        with self.assertRaises(SkillManifestError):
            validate_manifest(candidate)
        candidate = dict(self.value, protocol_version=True)
        with self.assertRaises(SkillManifestError):
            validate_manifest(candidate)

    def test_canonical_json_is_order_independent_and_finite(self):
        self.assertEqual(canonical_json_bytes({"b": 2, "a": 1}),
                         canonical_json_bytes({"a": 1, "b": 2}))
        with self.assertRaises(SkillManifestError):
            canonical_json_bytes({"value": float("inf")})

    def test_artifact_digest_verification_uses_exact_bytes(self):
        artifact = b"reviewed skill bundle"
        digest = hashlib.sha256(artifact).hexdigest()
        self.assertTrue(verify_artifact_digest(artifact, digest))
        self.assertFalse(verify_artifact_digest(artifact + b"!", digest))


if __name__ == "__main__":
    unittest.main()
