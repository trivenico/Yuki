import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from yuki_hf_model_scout import TASKS, main


class ScoutTests(unittest.TestCase):
    def test_task_scope_is_bounded_and_explicit(self):
        self.assertEqual(len(TASKS), 6)
        self.assertIn("text-generation", TASKS)
        self.assertIn("automatic-speech-recognition", TASKS)

    def test_rejects_invalid_limit_without_network(self):
        with self.assertRaises(SystemExit):
            main(["--limit-per-task", "0"])
        with self.assertRaises(SystemExit):
            main(["--limit-per-task", "51"])

    def test_writes_deduplicated_timestamped_report(self):
        class FakeDiscovery:
            def __init__(self, **kwargs):
                self.max_results = kwargs["max_results"]
            def discover(self, *, task, sort):
                self.assertion = (task, sort)
                return {
                    "models": [
                        {"model_id": "org/shared", "task": task, "last_modified": "2026-01-01T00:00:00Z", "license": "mit"},
                        {"model_id": "org/" + task, "task": task, "last_modified": "2026-01-02T00:00:00Z", "license": None},
                    ]
                }
        with tempfile.TemporaryDirectory() as tmp, patch("yuki_hf_model_scout.HuggingFaceDiscovery", FakeDiscovery):
            result = main(["--limit-per-task", "2", "--output-dir", tmp])
            self.assertEqual(result, 0)
            files = list(Path(tmp).glob("hf-model-scout-*.json"))
            self.assertEqual(len(files), 1)
            report = json.loads(files[0].read_text(encoding="utf-8"))
            ids = [item["model_id"] for item in report["models"]]
            self.assertEqual(len(ids), len(set(ids)))
            self.assertFalse(report["review_policy"]["automatic_activation"])
            self.assertFalse(report["review_policy"]["downloads_weights"])
            self.assertFalse(report["review_policy"]["executes_remote_code"])
            self.assertEqual(report["scope"]["limit_per_task"], 2)


if __name__ == "__main__":
    unittest.main()
