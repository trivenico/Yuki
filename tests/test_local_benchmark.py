import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from evaluation.local_benchmark import load_benchmark, run_local_benchmark


class LocalBenchmarkTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "suite.json"
        self.payload = {
            "name": "unit suite", "version": 1,
            "cases": [{"id": "case-one", "task": "general",
                       "prompt": "Say hello", "expected_terms": ["hello"]}],
        }
        self.path.write_text(json.dumps(self.payload), encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def test_loader_fingerprints_exact_dataset_bytes(self):
        result = load_benchmark(self.path)
        self.assertEqual(result["name"], "unit suite")
        self.assertEqual(len(result["sha256"]), 64)
        self.assertEqual(result["cases"][0].required_terms, ("hello",))

    def test_loader_rejects_duplicate_ids_and_invalid_version(self):
        self.payload["cases"].append(dict(self.payload["cases"][0]))
        self.path.write_text(json.dumps(self.payload), encoding="utf-8")
        with self.assertRaises(ValueError):
            load_benchmark(self.path)
        self.payload["cases"].pop()
        self.payload["version"] = True
        self.path.write_text(json.dumps(self.payload), encoding="utf-8")
        with self.assertRaises(ValueError):
            load_benchmark(self.path)

    def test_runner_requires_available_local_provider_and_never_activates(self):
        provider = Mock()
        provider.is_available.return_value = True
        provider.generate.return_value = "hello there"
        report = run_local_benchmark(provider, "qwen3:4b", self.path)
        self.assertEqual(report["inference_backend"], "ollama_local")
        self.assertEqual(report["evaluation"]["passed_count"], 1)
        self.assertEqual(report["benchmark"]["sha256"], load_benchmark(self.path)["sha256"])
        self.assertFalse(report["automatic_activation"])
        self.assertEqual(report["promotion_policy"], "human_review_required")

    def test_runner_fails_if_local_model_is_unavailable(self):
        provider = Mock()
        provider.is_available.return_value = False
        with self.assertRaises(RuntimeError):
            run_local_benchmark(provider, "qwen3:4b", self.path)
        provider.generate.assert_not_called()

    def test_report_does_not_include_prompts_or_model_responses(self):
        provider = Mock()
        provider.is_available.return_value = True
        provider.generate.return_value = "hello sensitive generated output"
        report = run_local_benchmark(provider, "qwen3:4b", self.path)
        rendered = json.dumps(report)
        self.assertNotIn("Say hello", rendered)
        self.assertNotIn("sensitive generated output", rendered)


if __name__ == "__main__":
    unittest.main()
