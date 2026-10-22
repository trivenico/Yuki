import json
import unittest
from unittest.mock import patch
from providers.huggingface_discovery import (
    DiscoveryError, HuggingFaceDiscovery, normalize_model,
)


class _Response:
    def __init__(self, data):
        self.data = data
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False
    def read(self, limit=-1):
        return self.data[:limit]


class _Opener:
    def __init__(self, payload):
        self.payload = payload
        self.request = None
        self.timeout = None
    def open(self, request, timeout):
        self.request, self.timeout = request, timeout
        return _Response(self.payload)


class HuggingFaceDiscoveryTests(unittest.TestCase):
    def test_normalizes_model_and_flags_unknown_license(self):
        item = normalize_model({"id":"org/model","pipeline_tag":"text-generation",
            "downloads":123,"likes":9,"tags":["license:apache-2.0","transformers"]})
        self.assertEqual(item["model_id"], "org/model")
        self.assertEqual(item["license"], "apache-2.0")
        self.assertEqual(item["review_status"], "needs_human_review")
        self.assertFalse(item["automatic_activation"])

        unknown = normalize_model({"id":"org/unknown","pipeline_tag":"text-generation"})
        self.assertIn("license_not_reported", unknown["review_flags"])

    def test_invalid_model_ids_are_rejected(self):
        self.assertIsNone(normalize_model({"id":"../../etc/passwd"}))
        self.assertIsNone(normalize_model({"id":"org/model?revision=main"}))
        self.assertIsNone(normalize_model("not-an-object"))

    def test_discovery_is_bounded_deduplicated_and_source_attributed(self):
        payload = json.dumps([
            {"id":"org/model","pipeline_tag":"text-generation","downloads":5},
            {"id":"org/model","pipeline_tag":"text-generation","downloads":5},
            {"id":"org/second","pipeline_tag":"text-generation","downloads":2},
        ]).encode()
        opener = _Opener(payload)
        result = HuggingFaceDiscovery(max_results=3, opener=opener).discover()
        self.assertEqual(result["count"], 2)
        self.assertEqual(result["source"], "huggingface_hub_public_model_metadata")
        self.assertEqual(result["activation_policy"], "manual_review_required")
        self.assertEqual(opener.timeout, 10)
        self.assertIn("filter=text-generation", opener.request.full_url)
        self.assertEqual(opener.request.get_method(), "GET")

    def test_task_sort_and_bounds_are_validated(self):
        discovery = HuggingFaceDiscovery()
        with self.assertRaises(ValueError):
            discovery.discover(task="arbitrary-task")
        with self.assertRaises(ValueError):
            discovery.discover(sort="private-field")
        with self.assertRaises(ValueError):
            HuggingFaceDiscovery(max_results=1000)
        with self.assertRaises(ValueError):
            HuggingFaceDiscovery(timeout=1000)

    def test_token_is_never_added_to_url(self):
        opener = _Opener(b"[]")
        HuggingFaceDiscovery(opener=opener).discover(token="hf_test_token")
        self.assertNotIn("hf_test_token", opener.request.full_url)
        self.assertEqual(opener.request.get_header("Authorization"), "Bearer hf_test_token")

    def test_invalid_json_fails_closed(self):
        with self.assertRaises(DiscoveryError):
            HuggingFaceDiscovery(opener=_Opener(b"not-json")).discover()

    @patch("providers.huggingface_discovery.datetime")
    def test_discovery_timestamp_is_utc(self, mock_datetime):
        mock_datetime.now.return_value.isoformat.return_value = "2026-10-22T00:00:00+00:00"
        result = HuggingFaceDiscovery(opener=_Opener(b"[]")).discover()
        self.assertIn("+00:00", result["queried_at"])


if __name__ == "__main__":
    unittest.main()
