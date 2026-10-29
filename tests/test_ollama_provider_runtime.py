"""Runtime and failure-mode tests for the local Ollama provider."""
import http.client
import json
import unittest
import urllib.error
from unittest.mock import patch

from providers.ollama import OllamaProvider, _NoRedirectHandler


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.closed = True

    def read(self, limit=-1):
        return self.payload if limit < 0 else self.payload[:limit]


class OllamaProviderRuntimeTests(unittest.TestCase):
    def provider(self, **kwargs):
        defaults = {"base_url": "http://127.0.0.1:11434", "model": "llama3.2:3b"}
        defaults.update(kwargs)
        return OllamaProvider(**defaults)

    @patch("providers.ollama.urllib.request.urlopen")
    def test_availability_requires_configured_model(self, urlopen):
        urlopen.return_value = FakeResponse(json.dumps({
            "models": [{"name": "llama3.2:3b"}, {"name": "other:latest"}]
        }).encode())
        self.assertTrue(self.provider().is_available())
        request = urlopen.call_args.args[0]
        self.assertEqual(request.full_url, "http://127.0.0.1:11434/api/tags")
        self.assertEqual(request.get_method(), "GET")
        self.assertLessEqual(urlopen.call_args.kwargs["timeout"], 5)

    @patch("providers.ollama.urllib.request.build_opener")
    def test_availability_uses_no_redirect_handler(self, build_opener):
        build_opener.return_value.open.return_value = FakeResponse(json.dumps({
            "models": [{"name": "llama3.2:3b"}]
        }).encode())
        self.assertTrue(self.provider().is_available())
        build_opener.assert_called_once_with(_NoRedirectHandler)

    @patch("providers.ollama.urllib.request.urlopen")
    def test_availability_returns_false_when_model_is_not_installed(self, urlopen):
        urlopen.return_value = FakeResponse(json.dumps({
            "models": [{"name": "other:latest"}]
        }).encode())
        self.assertFalse(self.provider().is_available())

    @patch("providers.ollama.urllib.request.urlopen")
    def test_availability_fails_closed_on_unexpected_payload_shape(self, urlopen):
        urlopen.return_value = FakeResponse(json.dumps({"models": "not-a-list"}).encode())
        self.assertFalse(self.provider().is_available())

    @patch("providers.ollama.urllib.request.urlopen")
    def test_availability_fails_closed_on_bad_json(self, urlopen):
        urlopen.return_value = FakeResponse(b"not-json")
        self.assertFalse(self.provider().is_available())

    @patch("providers.ollama.urllib.request.urlopen")
    def test_availability_returns_false_when_server_is_down(self, urlopen):
        urlopen.side_effect = urllib.error.URLError("offline")
        self.assertFalse(self.provider().is_available())

    @patch("providers.ollama.urllib.request.urlopen")
    def test_availability_fails_closed_on_http_protocol_error(self, urlopen):
        urlopen.side_effect = http.client.HTTPException("truncated HTTP response")
        self.assertFalse(self.provider().is_available())

    @patch("providers.ollama.urllib.request.urlopen")
    def test_generate_sends_non_streaming_request_and_returns_text(self, urlopen):
        urlopen.return_value = FakeResponse(json.dumps({
            "response": " YUKI_OK "
        }).encode())
        self.assertEqual(self.provider().generate(" hello "), "YUKI_OK")
        request = urlopen.call_args.args[0]
        payload = json.loads(request.data.decode())
        self.assertEqual(request.full_url, "http://127.0.0.1:11434/api/generate")
        self.assertEqual(payload, {
            "model": "llama3.2:3b", "prompt": "hello", "stream": False
        })
        self.assertEqual(request.get_method(), "POST")

    @patch("providers.ollama.urllib.request.build_opener")
    def test_generate_uses_no_redirect_handler(self, build_opener):
        build_opener.return_value.open.return_value = FakeResponse(json.dumps({
            "response": "YUKI_OK"
        }).encode())
        self.assertEqual(self.provider().generate("hello"), "YUKI_OK")
        build_opener.assert_called_once_with(_NoRedirectHandler)

    @patch("providers.ollama.urllib.request.urlopen")
    def test_generate_wraps_network_errors_without_leaking_details(self, urlopen):
        urlopen.side_effect = urllib.error.URLError("private endpoint detail")
        with self.assertRaisesRegex(RuntimeError, "Local Ollama request failed") as raised:
            self.provider().generate("hello")
        self.assertNotIn("private endpoint detail", str(raised.exception))

    @patch("providers.ollama.urllib.request.urlopen")
    def test_generate_wraps_http_protocol_errors(self, urlopen):
        urlopen.side_effect = http.client.HTTPException("truncated HTTP response")
        with self.assertRaisesRegex(RuntimeError, "Local Ollama request failed"):
            self.provider().generate("hello")

    @patch("providers.ollama.urllib.request.urlopen")
    def test_generate_rejects_invalid_json(self, urlopen):
        urlopen.return_value = FakeResponse(b"not-json")
        with self.assertRaisesRegex(RuntimeError, "invalid JSON"):
            self.provider().generate("hello")

    @patch("providers.ollama.urllib.request.urlopen")
    def test_generate_rejects_empty_response_text(self, urlopen):
        urlopen.return_value = FakeResponse(json.dumps({"response": "  "}).encode())
        with self.assertRaisesRegex(RuntimeError, "no text response"):
            self.provider().generate("hello")

    @patch("providers.ollama.urllib.request.urlopen")
    def test_generate_rejects_oversized_response(self, urlopen):
        urlopen.return_value = FakeResponse(b"x" * 2_000_001)
        with self.assertRaisesRegex(RuntimeError, "2 MB limit"):
            self.provider().generate("hello")

    def test_redirect_handler_refuses_redirects(self):
        handler = _NoRedirectHandler()
        self.assertIsNone(handler.redirect_request(
            None, None, 302, "Found", {}, "https://remote.example.invalid/collect"
        ))

    def test_rejects_insecure_non_loopback_http(self):
        with self.assertRaisesRegex(ValueError, "HTTPS or loopback HTTP"):
            self.provider(base_url="http://ollama.example.com")

    def test_rejects_url_credentials(self):
        with self.assertRaisesRegex(ValueError, "HTTPS or loopback HTTP"):
            self.provider(base_url="https://user:secret@ollama.example.com")

    def test_rejects_invalid_timeout(self):
        with self.assertRaisesRegex(ValueError, "Timeout"):
            self.provider(timeout=0)

    def test_rejects_boolean_timeout(self):
        with self.assertRaisesRegex(ValueError, "Timeout"):
            self.provider(timeout=True)

    def test_rejects_empty_prompt(self):
        with self.assertRaisesRegex(ValueError, "non-empty"):
            self.provider().generate("   ")


if __name__ == "__main__":
    unittest.main()
