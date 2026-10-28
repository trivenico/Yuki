import unittest

from urllib.error import HTTPError
from unittest.mock import patch

from core.readiness import _NoRedirectHandler, _probe_ollama, build_readiness_report


class RuntimeReadinessTests(unittest.TestCase):
    def test_report_contains_platform_and_capability_states(self):
        report = build_readiness_report(
            environ={"YUKI_OLLAMA_MODEL": "example-model"},
            executable_finder=lambda name: None,
            module_finder=lambda name: None,
        )
        data = report.to_dict()
        self.assertTrue(data["python"])
        self.assertTrue(data["operating_system"])
        self.assertTrue(data["architecture"])
        self.assertEqual(data["capabilities"]["ollama_cli"]["status"], "unavailable")
        self.assertEqual(data["capabilities"]["ollama_model_config"]["status"], "configured")
        self.assertEqual(data["capabilities"]["pytorch"]["status"], "unavailable")
        self.assertEqual(data["capabilities"]["onnxruntime"]["status"], "unavailable")

    def test_report_never_claims_model_or_accelerator_is_verified(self):
        report = build_readiness_report(
            environ={"YUKI_OLLAMA_MODEL": "example-model"},
            executable_finder=lambda name: "/usr/bin/ollama",
            module_finder=lambda name: object(),
        ).to_dict()
        self.assertIn("not verified", report["capabilities"]["ollama_cli"]["detail"])
        self.assertIn("not verified", report["capabilities"]["ollama_model_config"]["detail"])
        self.assertIn("not verified", report["capabilities"]["pytorch"]["detail"])
        self.assertIn("not verified", report["capabilities"]["onnxruntime"]["detail"])

    def test_empty_environment_reports_model_unconfigured(self):
        report = build_readiness_report(
            environ={},
            executable_finder=lambda name: None,
            module_finder=lambda name: None,
        )
        self.assertEqual(report.capabilities["ollama_model_config"].status, "unconfigured")


class OllamaProbeSecurityTests(unittest.TestCase):
    def test_redirect_handler_refuses_redirects(self):
        handler = _NoRedirectHandler()
        request = __import__("urllib.request", fromlist=["Request"]).Request(
            "http://127.0.0.1:11434/api/tags"
        )
        self.assertIsNone(
            handler.redirect_request(request, None, 302, "Found", {}, "https://example.com/collect")
        )

    def test_default_probe_uses_redirect_blocking_opener(self):
        response = type("Response", (), {
            "status": 200,
            "__enter__": lambda self: self,
            "__exit__": lambda self, *args: False,
            "read": lambda self, limit: b'{"models": []}',
        })()
        opener = type("Opener", (), {"open": lambda self, request, timeout: response})()
        with patch("core.readiness.build_opener", return_value=opener) as build:
            result = _probe_ollama("http://127.0.0.1:11434", timeout=1.0)
        self.assertEqual(result.status, "verified")
        self.assertIsInstance(build.call_args.args[0], _NoRedirectHandler)

    def test_non_loopback_endpoint_is_rejected_without_opening(self):
        with patch("core.readiness.build_opener") as build:
            result = _probe_ollama("http://example.com:11434")
        self.assertEqual(result.status, "unavailable")
        build.assert_not_called()


if __name__ == "__main__":
    unittest.main()
