import unittest

from providers.ollama_model_inventory import InstalledModel
from providers.model_readiness import assess_local_model_readiness


class ModelReadinessTests(unittest.TestCase):
    def setUp(self):
        self.digest = "a" * 64
        self.installed = [InstalledModel("yuki-core:latest", self.digest, 100,
                                         "1B", "Q4_K_M", "llama")]
        self.catalog = {"schema_version": 1, "models": [{
            "model_id": "yuki/core", "model_version": "0.1.0",
            "artifact_sha256": self.digest, "ownership_status": "verified",
            "state": "reviewed",
        }]}
        self.registry = {"schema_version": 1, "records": [{
            "model_id": "yuki/core", "benchmark_id": "core-v1",
            "artifact_sha256": self.digest, "coverage": 0.9,
            "mean_latency_ms": 12.0, "successful_cases": 10,
            "license_status": "verified", "runtime_status": "verified",
        }]}
        self.hardware = {
            "available_memory_bytes": 10_000,
            "gpu": {"memory_bytes": None},
        }

    def test_complete_evidence_is_only_human_review_eligible(self):
        report = assess_local_model_readiness(
            self.installed, self.catalog, self.registry, self.hardware,
            benchmark_id="core-v1",
        )
        self.assertEqual(len(report["recommendations"]), 1)
        candidate = report["recommendations"][0]
        self.assertEqual(candidate["recommendation_status"], "eligible_for_human_review")
        self.assertEqual(candidate["activation_status"], "manual_approval_required")
        self.assertFalse(report["automatic_activation"])

    def test_unverified_ownership_blocks_candidate(self):
        self.catalog["models"][0]["ownership_status"] = "unverified"
        report = assess_local_model_readiness(
            self.installed, self.catalog, self.registry, self.hardware,
            benchmark_id="core-v1",
        )
        self.assertIn("ownership_not_verified", report["excluded_candidates"][0]["blockers"])

    def test_wrong_evaluation_digest_blocks_candidate(self):
        self.registry["records"][0]["artifact_sha256"] = "b" * 64
        report = assess_local_model_readiness(
            self.installed, self.catalog, self.registry, self.hardware,
            benchmark_id="core-v1",
        )
        self.assertIn("evaluation_artifact_digest_mismatch", report["excluded_candidates"][0]["blockers"])

    def test_missing_memory_and_missing_evaluation_block_candidate(self):
        self.hardware["available_memory_bytes"] = None
        self.registry["records"] = []
        report = assess_local_model_readiness(
            self.installed, self.catalog, self.registry, self.hardware,
            benchmark_id="core-v1",
        )
        blockers = report["excluded_candidates"][0]["blockers"]
        self.assertIn("host_memory_snapshot_missing", blockers)
        self.assertIn("benchmark_evidence_missing", blockers)

    def test_report_does_not_mutate_inputs(self):
        before = repr((self.catalog, self.registry, self.hardware))
        assess_local_model_readiness(
            self.installed, self.catalog, self.registry, self.hardware,
            benchmark_id="core-v1",
        )
        self.assertEqual(repr((self.catalog, self.registry, self.hardware)), before)


if __name__ == "__main__":
    unittest.main()
