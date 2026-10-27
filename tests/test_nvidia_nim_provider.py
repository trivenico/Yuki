import json
import unittest
from unittest.mock import patch
from urllib.error import URLError

from providers.nvidia_nim import NvidiaNIMProvider


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


class NvidiaNIMProviderTests(unittest.TestCase):
    def test_request_uses_trusted_endpoint_and_bearer_header(self):
        payload = json.dumps({
            "choices": [{"message": {"content": "Hello from NVIDIA"}}]
        }).encode()
        opener = FakeOpener(FakeResponse(payload))
        provider = NvidiaNIMProvider("openai/gpt-oss-20b", environ={"NVIDIA_API_KEY": "test-secret"}, approval_callback=lambda prompt: True)
        with patch("providers.openai_compatible.urllib.request.build_opener", return_value=opener):
            self.assertEqual(provider.generate("hello"), "Hello from NVIDIA")
        self.assertEqual(opener.request.full_url, "https://integrate.api.nvidia.com/v1/chat/completions")
        self.assertEqual(opener.request.get_header("Authorization"), "Bearer test-secret")
        self.assertNotIn("test-secret", opener.request.full_url)
        self.assertEqual(opener.timeout, 45)

    def test_missing_key_fails_before_network(self):
        provider = NvidiaNIMProvider("openai/gpt-oss-20b", environ={})
        with patch("providers.openai_compatible.urllib.request.build_opener") as build:
            with self.assertRaisesRegex(RuntimeError, "NVIDIA_API_KEY"):
                provider.generate("hello")
        build.assert_not_called()

    def test_oversized_response_is_rejected(self):
        opener = FakeOpener(FakeResponse(b"x" * 2_000_001))
        provider = NvidiaNIMProvider("openai/gpt-oss-20b", environ={"NVIDIA_API_KEY": "secret"}, approval_callback=lambda prompt: True)
        with patch("providers.openai_compatible.urllib.request.build_opener", return_value=opener):
            with self.assertRaisesRegex(RuntimeError, "2 MB"):
                provider.generate("hello")

    def test_malformed_response_does_not_expose_payload(self):
        opener = FakeOpener(FakeResponse(b'{"error":"private-detail"}'))
        provider = NvidiaNIMProvider("openai/gpt-oss-20b", environ={"NVIDIA_API_KEY": "secret"}, approval_callback=lambda prompt: True)
        with patch("providers.openai_compatible.urllib.request.build_opener", return_value=opener):
            with self.assertRaisesRegex(RuntimeError, "unsupported response") as error:
                provider.generate("hello")
        self.assertNotIn("private-detail", str(error.exception))

    def test_network_errors_are_sanitized(self):
        opener = FakeOpener(error=URLError("private endpoint details"))
        provider = NvidiaNIMProvider("openai/gpt-oss-20b", environ={"NVIDIA_API_KEY": "secret"}, approval_callback=lambda prompt: True)
        with patch("providers.openai_compatible.urllib.request.build_opener", return_value=opener):
            with self.assertRaisesRegex(RuntimeError, "request failed") as error:
                provider.generate("hello")
        self.assertNotIn("private endpoint details", str(error.exception))

    def test_invalid_model_and_timeout_are_rejected(self):
        with self.assertRaises(ValueError):
            NvidiaNIMProvider("", environ={})
        with self.assertRaises(ValueError):
            NvidiaNIMProvider("model", timeout=0, environ={})


if __name__ == "__main__":
    unittest.main()
