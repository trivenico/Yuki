import copy
import unittest

from providers.local_model_profile import (
    LocalModelProfileError,
    admit_catalog_profile,
    profile_digest,
    validate_local_model_profile,
)


class LocalModelProfileTests(unittest.TestCase):
    def setUp(self):
        self.profile = {
            "schema_version": 1,
            "model_id": "yuki/compact",
            "model_version": "1.0.0",
            "artifact_sha256": "a" * 64,
            "source_url": "https://models.example.org/yuki/compact.gguf",
            "license_id": "Apache-2.0",
            "format": "gguf",
            "quantization": "q4_k_m",
            "minimum_ram_bytes": 512 * 1024**2,
            "recommended_ram_bytes": 1024 * 1024**2,
            "artifact_bytes": 256 * 1024**2,
            "minimum_free_disk_bytes": 512 * 1024**2,
            "supported_operating_systems": ["Windows", "Linux", "Darwin"],
            "supported_architectures": ["x86_64", "AMD64", "aarch64", "arm64"],
            "requires_gpu": False,
            "minimum_vram_bytes": None,
            "context_tokens": 4096,
            "runtime": "ollama",
        }
        self.catalog = {"schema_version": 1, "models": [{
            "model_id": "yuki/compact", "model_version": "1.0.0",
            "artifact_sha256": "a" * 64, "license_id": "Apache-2.0",
            "state": "approved", "provenance_status": "verified",
            "license_status": "verified", "evaluation_passed": True,
        }]}

    def test_profile_validates_and_has_stable_digest(self):
        normalized = validate_local_model_profile(self.profile)
        self.assertEqual(normalized["model_id"], "yuki/compact")
        self.assertEqual(profile_digest(self.profile), profile_digest(dict(reversed(list(self.profile.items())))))

    def test_mutation_changes_profile_digest(self):
        changed = copy.deepcopy(self.profile)
        changed["context_tokens"] = 8192
        self.assertNotEqual(profile_digest(self.profile), profile_digest(changed))

    def test_unknown_fields_and_unsafe_urls_are_rejected(self):
        changed = dict(self.profile, install_command="curl | sh")
        with self.assertRaisesRegex(LocalModelProfileError, "fields"):
            validate_local_model_profile(changed)
        changed = dict(self.profile, source_url="https://user:pass@example.org/model")
        with self.assertRaisesRegex(LocalModelProfileError, "credentials"):
            validate_local_model_profile(changed)

    def test_profile_rejects_inconsistent_resource_metadata(self):
        changed = dict(self.profile, recommended_ram_bytes=1)
        with self.assertRaisesRegex(LocalModelProfileError, "recommended_ram"):
            validate_local_model_profile(changed)
        changed = dict(self.profile, minimum_free_disk_bytes=1)
        with self.assertRaisesRegex(LocalModelProfileError, "minimum_disk"):
            validate_local_model_profile(changed)

    def test_catalog_approval_and_provenance_are_required(self):
        self.catalog["models"][0]["state"] = "quarantined"
        with self.assertRaisesRegex(LocalModelProfileError, "not_approved"):
            admit_catalog_profile(self.profile, self.catalog, os_name="Linux",
                                  architecture="x86_64", runtime_budget=2**30, free_disk=2**30)
        self.catalog["models"][0]["state"] = "approved"
        self.catalog["models"][0]["license_status"] = "unknown"
        with self.assertRaisesRegex(LocalModelProfileError, "unverified"):
            admit_catalog_profile(self.profile, self.catalog, os_name="Linux",
                                  architecture="x86_64", runtime_budget=2**30, free_disk=2**30)

    def test_catalog_digest_and_license_must_match_profile(self):
        self.catalog["models"][0]["artifact_sha256"] = "b" * 64
        with self.assertRaisesRegex(LocalModelProfileError, "digest_mismatch"):
            admit_catalog_profile(self.profile, self.catalog, os_name="Linux",
                                  architecture="x86_64", runtime_budget=2**30, free_disk=2**30)
        self.catalog["models"][0]["artifact_sha256"] = "a" * 64
        self.catalog["models"][0]["license_id"] = "MIT"
        with self.assertRaisesRegex(LocalModelProfileError, "license_mismatch"):
            admit_catalog_profile(self.profile, self.catalog, os_name="Linux",
                                  architecture="x86_64", runtime_budget=2**30, free_disk=2**30)

    def test_approved_profile_is_scored_against_device(self):
        result = admit_catalog_profile(
            self.profile, self.catalog, os_name="Linux", architecture="x86_64",
            runtime_budget=2 * 1024**3, free_disk=2 * 1024**3,
        )
        self.assertEqual(result["admission"], "eligible_for_runtime_preflight")
        self.assertTrue(result["readiness"]["recommended"])

    def test_gpu_model_requires_verified_sufficient_vram(self):
        profile = dict(self.profile, requires_gpu=True, minimum_vram_bytes=4 * 1024**3)
        result = admit_catalog_profile(
            profile, self.catalog, os_name="Linux", architecture="x86_64",
            runtime_budget=2 * 1024**3, free_disk=2 * 1024**3,
        )
        self.assertEqual(result["admission"], "rejected")
        self.assertIn("insufficient_or_unverified_gpu_memory", result["readiness"]["reasons"])
        result = admit_catalog_profile(
            profile, self.catalog, os_name="Linux", architecture="x86_64",
            runtime_budget=2 * 1024**3, free_disk=2 * 1024**3,
            verified_gpu_memory_bytes=8 * 1024**3,
        )
        self.assertEqual(result["admission"], "eligible_for_runtime_preflight")

    def test_model_evaluation_must_have_passed(self):
        self.catalog["models"][0]["evaluation_passed"] = False
        with self.assertRaisesRegex(LocalModelProfileError, "evaluation"):
            admit_catalog_profile(self.profile, self.catalog, os_name="Linux",
                                  architecture="x86_64", runtime_budget=2**30, free_disk=2**30)


if __name__ == "__main__":
    unittest.main()
