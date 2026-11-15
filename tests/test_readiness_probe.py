import unittest
from unittest.mock import patch

from core.readiness import Capability, _probe_ollama, build_readiness_report


class FakeResponse:
    status = 200
    def __init__(self, payload):
        self.payload = payload
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False
    def read(self, limit):
        self.limit = limit
        return self.payload


class ReadinessProbeTests(unittest.TestCase):
    def test_successful_local_tags_endpoint_is_verified_not_inference(self):
        response = FakeResponse(b'{"models": []}')
        class Opener:
            def open(self, request, timeout):
                self.request = request
                self.timeout = timeout
                return response
        opener = Opener()
        with patch("core.readiness.build_opener", return_value=opener) as build:
            result = _probe_ollama("http://127.0.0.1:11434", timeout=1.0)
        self.assertEqual(result.status, "verified")
        self.assertIn("inference was not tested", result.detail)
        self.assertEqual(opener.timeout, 1.0)
        self.assertEqual(opener.request.full_url, "http://127.0.0.1:11434/api/tags")
        self.assertEqual(build.call_count, 1)
        self.assertEqual(response.limit, 1_000_001)

    def test_non_loopback_endpoints_are_rejected_without_network(self):
        with patch("core.readiness.build_opener") as opener:
            result = _probe_ollama("http://192.0.2.1:11434")
        self.assertEqual(result.status, "unavailable")
        opener.assert_not_called()

    def test_credentials_and_query_are_rejected(self):
        for url in ("http://user:pass@localhost:11434", "http://localhost:11434?token=secret", "https://example.com:11434"):
            with self.subTest(url=url), patch("core.readiness.build_opener") as opener:
                self.assertEqual(_probe_ollama(url).status, "unavailable")
                opener.assert_not_called()

    def test_invalid_json_and_timeout_fail_closed(self):
        class Opener:
            def __init__(self, response=None, error=None):
                self.response, self.error = response, error
            def open(self, request, timeout):
                if self.error:
                    raise self.error
                return self.response
        with patch("core.readiness.build_opener", return_value=Opener(response=FakeResponse(b"not-json"))):
            self.assertEqual(_probe_ollama("http://127.0.0.1:11434").status, "unavailable")
        with patch("core.readiness.build_opener", return_value=Opener(error=TimeoutError())):
            result = _probe_ollama("http://127.0.0.1:11434")
            self.assertEqual(result.status, "unavailable")
            self.assertNotIn("secret", result.detail)

    def test_invalid_timeout_is_rejected(self):
        self.assertEqual(_probe_ollama("http://localhost:11434", timeout=10).status, "unavailable")

    def test_report_uses_injected_probe_and_keeps_detection_distinct(self):
        report = build_readiness_report(
            environ={"YUKI_OLLAMA_MODEL": "test-model"},
            executable_finder=lambda name: "/usr/bin/ollama",
            module_finder=lambda name: None,
            ollama_probe=lambda url, timeout: Capability("verified", "fake probe"),
        )
        self.assertEqual(report.capabilities["ollama_server"].status, "verified")
        self.assertEqual(report.capabilities["ollama_model_config"].status, "configured")
        self.assertEqual(report.capabilities["pytorch"].status, "unavailable")


if __name__ == "__main__":
    unittest.main()
