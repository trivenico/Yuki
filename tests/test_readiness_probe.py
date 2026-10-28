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
        with patch("core.readiness.build_opener") as builder:
            opener = builder.return_value
            opener.open.return_value = response
            result = _probe_ollama("http://127.0.0.1:11434", timeout=1.0)
        self.assertEqual(result.status, "verified")
        self.assertIn("inference was not tested", result.detail)
        self.assertEqual(opener.open.call_args.kwargs["timeout"], 1.0)
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

    def test_missing_or_invalid_models_field_is_not_verified(self):
        for payload in (b'{}', b'{"models": null}', b'{"models": {}}'):
            with self.subTest(payload=payload), patch("core.readiness.build_opener") as builder:
                builder.return_value.open.return_value = FakeResponse(payload)
                result = _probe_ollama("http://localhost:11434")
            self.assertEqual(result.status, "unavailable")

    def test_invalid_json_and_timeout_fail_closed(self):
        with patch("core.readiness.build_opener") as builder:
            builder.return_value.open.return_value = FakeResponse(b"not-json")
            self.assertEqual(_probe_ollama("http://localhost:11434").status, "unavailable")
        with patch("core.readiness.build_opener") as builder:
            builder.return_value.open.side_effect = TimeoutError
            result = _probe_ollama("http://localhost:11434")
            self.assertEqual(result.status, "unavailable")
            self.assertNotIn("secret", result.detail)

    def test_invalid_timeout_is_rejected(self):
        self.assertEqual(_probe_ollama("http://localhost:11434", timeout=10).status, "unavailable")

    def test_server_is_probed_even_when_cli_is_not_on_path(self):
        probe = lambda url, timeout: Capability("verified", "local server responded")
        report = build_readiness_report(
            environ={},
            executable_finder=lambda name: None,
            module_finder=lambda name: None,
            ollama_probe=probe,
        )
        self.assertEqual(report.capabilities["ollama_cli"].status, "unavailable")
        self.assertEqual(report.capabilities["ollama_server"].status, "verified")

    def test_executable_finder_is_called_once_for_ollama_cli(self):
        calls = []
        def finder(name):
            calls.append(name)
            return None
        build_readiness_report(
            environ={},
            executable_finder=finder,
            module_finder=lambda name: None,
            ollama_probe=lambda url, timeout: Capability("unavailable", "not running"),
        )
        self.assertEqual(calls.count("ollama"), 1)

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
