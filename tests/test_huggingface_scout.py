import unittest
from unittest.mock import patch

from integrations.huggingface_scout import HuggingFaceScout

class Response:
    status = 200
    def __init__(self, payload): self.payload = payload
    def __enter__(self): return self
    def __exit__(self, *args): return False
    def read(self, n=-1): return self.payload[:n]

class HuggingFaceScoutTests(unittest.TestCase):
    def test_search_deduplicates_and_marks_review_required(self):
        payload = b'[{"id":"org/yuki-agent","lastModified":"2026-10-20T10:00:00Z","downloads":42,"likes":3,"tags":["agents","license:apache-2.0"],"pipeline_tag":"text-generation"}]'
        scout = HuggingFaceScout(max_results_per_query=5)
        with patch("integrations.huggingface_scout.urlopen", return_value=Response(payload)) as mocked:
            result = scout.search(kinds=("models",), queries={"models":("agent", "coding")}, limit=10)
        self.assertEqual(result["result_count"], 1)
        item = result["results"][0]
        self.assertEqual(item["repo_id"], "org/yuki-agent")
        self.assertIn("license_and_security_review", item["review_status"])
        self.assertFalse(result["automatic_installation"])
        self.assertFalse(result["automatic_execution"])
        self.assertEqual(mocked.call_count, 2)

    def test_rejects_arbitrary_url(self):
        scout = HuggingFaceScout()
        with self.assertRaises(ValueError): scout._get_json("https://evil.example/api/models?search=x")

    def test_limits_are_validated(self):
        with self.assertRaises(ValueError): HuggingFaceScout(timeout_seconds=60)
        with self.assertRaises(ValueError): HuggingFaceScout(max_results_per_query=500)
        with self.assertRaises(ValueError): HuggingFaceScout().search(kinds=("users",))
        with self.assertRaises(ValueError): HuggingFaceScout().search(limit=1000)

    def test_ignores_malformed_repo_records(self):
        payload = b'[{"id":"not-a-valid-repo"},{"id":"org/valid","downloads":-5,"likes":"many"}]'
        scout = HuggingFaceScout()
        with patch("integrations.huggingface_scout.urlopen", return_value=Response(payload)):
            result = scout.search(kinds=("models",), queries={"models":("agent",)}, limit=10)
        self.assertEqual(result["result_count"], 1)
        self.assertEqual(result["results"][0]["downloads"], 0)
        self.assertEqual(result["results"][0]["likes"], 0)

if __name__ == "__main__": unittest.main()
