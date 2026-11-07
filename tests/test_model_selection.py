import unittest

from core.local_model_registry import LocalModel
from core.model_capabilities import ModelCapability, ModelCapabilityRegistry
from core.model_selection import BenchmarkEvidence, select_local_models


class DigestBoundModelSelectionTests(unittest.TestCase):
    def setUp(self):
        self.model = LocalModel(
            "yuki-code:latest", "a" * 64, 1, None, "llama", "3B", "Q4_K_M"
        )

    def evidence(self, digest=None, score=0.91):
        return BenchmarkEvidence(
            "yuki-code:latest", digest or self.model.digest, "coding", score,
            "yuki-coding-eval", "1.0", "2026-10-01", "file://benchmarks/coding.json"
        )

    def registry(self, digest=None):
        return ModelCapabilityRegistry([
            ModelCapability(
                "yuki-code:latest", frozenset({"coding"}),
                "reviewed local runtime capability", True, "verified",
                digest or self.model.digest,
            )
        ])

    def test_exact_digest_and_benchmark_produce_candidate(self):
        result = select_local_models(
            "coding", [self.model], self.registry(), [self.evidence()],
            available_memory_gib=16,
        )
        self.assertEqual(result[0].status, "candidate")
        self.assertEqual(result[0].benchmark_score, 0.91)

    def test_digest_mismatch_is_not_recommended(self):
        result = select_local_models(
            "coding", [self.model], self.registry("b" * 64), [self.evidence()],
            available_memory_gib=16,
        )
        self.assertEqual(result[0].status, "digest_mismatch")

    def test_missing_benchmark_is_not_recommended(self):
        result = select_local_models(
            "coding", [self.model], self.registry(), [],
            available_memory_gib=16,
        )
        self.assertEqual(result[0].status, "benchmark_missing")

    def test_low_score_is_rejected(self):
        result = select_local_models(
            "coding", [self.model], self.registry(), [self.evidence(score=0.4)],
            available_memory_gib=16,
        )
        self.assertEqual(result[0].status, "below_threshold")

    def test_unknown_memory_does_not_claim_fit(self):
        result = select_local_models(
            "coding", [self.model], self.registry(), [self.evidence()],
            available_memory_gib=None,
        )
        self.assertEqual(result[0].status, "hardware_unknown")

    def test_invalid_digest_is_rejected(self):
        with self.assertRaises(ValueError):
            self.registry("not-a-digest")


if __name__ == "__main__":
    unittest.main()
