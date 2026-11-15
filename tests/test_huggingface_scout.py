import unittest

from integrations.huggingface_scout import HuggingFaceScout

class Response:
    status = 200
    def __init__(self, payload): self.payload = payload
    def __enter__(self): return self
    def __exit__(self, *args): return False
    def read(self, n=-1): return self.payload[:n]

class FakeTransport:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def open(self, request, timeout=None):
        self.calls.append((request, timeout))
        return Response(self.payload)


class HuggingFaceScoutTests(unittest.TestCase):
    def test_search_deduplicates_and_marks_review_required(self):
        payload = b'[{"id":"org/yuki-agent","lastModified":"2026-10-20T10:00:00Z","downloads":42,"likes":3,"tags":["agents","license:apache-2.0"],"pipeline_tag":"text-generation"}]'
        transport = FakeTransport(payload)
        scout = HuggingFaceScout(max_results_per_query=5, transport=transport)
        result = scout.search(kinds=("models",), queries={"models":("agent", "coding")}, limit=10)
        self.assertEqual(result["result_count"], 1)
        item = result["results"][0]
        self.assertEqual(item["repo_id"], "org/yuki-agent")
        self.assertEqual(item["review_status"], "needs_security_and_compatibility_review")
        self.assertFalse(result["automatic_installation"])
        self.assertFalse(result["automatic_execution"])
        self.assertEqual(len(transport.calls), 2)

    def test_default_transport_is_dns_pinned_to_hub_host(self):
        from core.pinned_https_provider_transport import PinnedHTTPSProviderTransport
        scout = HuggingFaceScout()
        self.assertIsInstance(scout._transport, PinnedHTTPSProviderTransport)
        self.assertEqual(scout._transport.allowed_hosts, frozenset({"huggingface.co"}))

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
        result = HuggingFaceScout(transport=FakeTransport(payload)).search(kinds=("models",), queries={"models":("agent",)}, limit=10)
        self.assertEqual(result["result_count"], 1)
        self.assertEqual(result["results"][0]["downloads"], 0)
        self.assertEqual(result["results"][0]["likes"], 0)

if __name__ == "__main__": unittest.main()
