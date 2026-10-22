import json
import tempfile
import unittest
from pathlib import Path
from providers.model_registry import (
    RegistryError, load_registry, record_evaluation, recommend, save_registry, validate_evaluation,
)


def report(model="org/model", score=0.8, latency=100.0):
    return {"schema_version":1,"model_id":model,"source":"huggingface_inference_router",
            "successful_cases":3,"failed_cases":0,"case_count":3,
            "mean_expected_term_coverage":score,"mean_latency_ms":latency}


class ModelRegistryTests(unittest.TestCase):
    def test_validation_sanitizes_report_and_requires_review(self):
        item = validate_evaluation(report(), benchmark_id="starter-v1",
                                  license_status="verified", runtime_status="verified")
        self.assertEqual(item["model_id"], "org/model")
        self.assertFalse(item["automatic_activation"])
        self.assertEqual(item["review_status"], "needs_human_review")

    def test_invalid_counts_scores_and_ids_rejected(self):
        bad = report()
        bad["successful_cases"] = 4
        with self.assertRaises(ValueError):
            validate_evaluation(bad, benchmark_id="starter-v1")
        bad = report(score=1.5)
        with self.assertRaises(ValueError):
            validate_evaluation(bad, benchmark_id="starter-v1")
        with self.assertRaises(ValueError):
            validate_evaluation(report("../bad"), benchmark_id="starter-v1")

    def test_recommendations_only_include_verified_candidates_and_same_benchmark(self):
        registry = {"schema_version":1,"records":[]}
        for model, score, latency, license_status, runtime_status, benchmark in [
            ("org/slow", .9, 200, "verified", "verified", "starter-v1"),
            ("org/fast", .9, 100, "verified", "verified", "starter-v1"),
            ("org/unverified", 1.0, 50, "unknown", "verified", "starter-v1"),
            ("org/other-benchmark", 1.0, 10, "verified", "verified", "other-v1"),
        ]:
            item = validate_evaluation(report(model, score, latency), benchmark_id=benchmark,
                                       license_status=license_status, runtime_status=runtime_status)
            record_evaluation(registry, item)
        result = recommend(registry, benchmark_id="starter-v1")
        self.assertEqual([x["model_id"] for x in result["recommendations"]], ["org/fast", "org/slow"])
        self.assertEqual(result["excluded_candidates"][0]["reason"], "license_not_verified")
        self.assertEqual(result["activation_policy"], "manual_review_required")

    def test_registry_upserts_same_model_and_benchmark(self):
        registry = {"schema_version":1,"records":[]}
        first = validate_evaluation(report(score=.5), benchmark_id="v1")
        second = validate_evaluation(report(score=.9), benchmark_id="v1")
        record_evaluation(registry, first)
        record_evaluation(registry, second)
        self.assertEqual(len(registry["records"]), 1)
        self.assertEqual(registry["records"][0]["coverage"], .9)

    def test_save_load_round_trip_and_invalid_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "nested" / "registry.json"
            save_registry(path, {"schema_version":1,"records":[]})
            self.assertEqual(load_registry(path), {"schema_version":1,"records":[]})
            path.write_text("{bad", encoding="utf-8")
            with self.assertRaises(RegistryError):
                load_registry(path)

    def test_save_does_not_overwrite_registry_semantics_unexpectedly(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "registry.json"
            save_registry(path, {"schema_version":1,"records":[]})
            save_registry(path, {"schema_version":1,"records":[validate_evaluation(report(), benchmark_id="v1")]})
            self.assertEqual(len(load_registry(path)["records"]), 1)


if __name__ == "__main__":
    unittest.main()
