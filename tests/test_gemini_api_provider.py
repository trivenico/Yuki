import json
import unittest
from unittest.mock import patch
from urllib.error import URLError

from providers.gemini_api import GeminiAPIProvider


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, limit):
        return self.payload[:limit]


class FakeOpener:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.request = None
        self.timeout = None

    def open(self, request, timeout):
        self.request = request
        self.timeout = timeout
        if self.error:
            raise self.error
        return self.response


class GeminiAPIProviderTests(unittest.TestCase):
    def test_successful_response_uses_api_key_header_and_extracts_text(self):
        payload = json.dumps({
            "candidates": [{
                "content": {"parts": [{"text": "Hello"}, {"text": " Yuki"}]}
            }]
        }).encode()
        opener = FakeOpener(FakeResponse(payload))
        provider = GeminiAPIProvider(environ={"GEMINI_API_KEY": "test-secret"})
        with patch("providers.gemini_api.urllib.request.build_opener", return_value=opener):
            self.assertEqual(provider.generate("  hello  "), "Hello Yuki")
        self.assertEqual(opener.request.get_header("X-goog-api-key"), "test-secret")
        self.assertEqual(opener.timeout, 45)
        self.assertNotIn("test-secret", opener.request.full_url)

    def test_missing_key_fails_before_network(self):
        provider = GeminiAPIProvider(environ={})
        with patch("providers.gemini_api.urllib.request.build_opener") as build:
            with self.assertRaisesRegex(RuntimeError, "GEMINI_API_KEY"):
                provider.generate("hello")
        build.assert_not_called()

    def test_oversized_response_is_rejected(self):
        opener = FakeOpener(FakeResponse(b"x" * 1_000_001))
        provider = GeminiAPIProvider(environ={"GEMINI_API_KEY": "secret"})
        with patch("providers.gemini_api.urllib.request.build_opener", return_value=opener):
            with self.assertRaisesRegex(RuntimeError, "1 MB"):
                provider.generate("hello")

    def test_malformed_response_is_rejected_without_raw_payload(self):
        opener = FakeOpener(FakeResponse(b'{"error":"private-provider-detail"}'))
        provider = GeminiAPIProvider(environ={"GEMINI_API_KEY": "secret"})
        with patch("providers.gemini_api.urllib.request.build_opener", return_value=opener):
            with self.assertRaisesRegex(RuntimeError, "invalid response") as error:
                provider.generate("hello")
        self.assertNotIn("private-provider-detail", str(error.exception))

    def test_network_errors_are_sanitized(self):
        opener = FakeOpener(error=URLError("private endpoint details"))
        provider = GeminiAPIProvider(environ={"GEMINI_API_KEY": "secret"})
        with patch("providers.gemini_api.urllib.request.build_opener", return_value=opener):
            with self.assertRaisesRegex(RuntimeError, "request failed") as error:
                provider.generate("hello")
        self.assertNotIn("private endpoint details", str(error.exception))

    def test_invalid_prompts_and_timeouts_are_rejected(self):
        with self.assertRaises(ValueError):
            GeminiAPIProvider(timeout=0)
        provider = GeminiAPIProvider(environ={"GEMINI_API_KEY": "secret"})
        with self.assertRaises(ValueError):
            provider.generate("  ")


if __name__ == "__main__":
    unittest.main()
